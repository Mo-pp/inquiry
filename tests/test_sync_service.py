"""仅用内存替身验证配置开关；不访问网络或数据库。"""
import unittest
from dataclasses import replace
from threading import Event, Thread
from unittest.mock import Mock

from app.settings import GoogleSheetsSettings
from app.leads.gateway_client import GatewayRows, GatewayTail
from app.leads.lead_mapper import EXPECTED_HEADERS
from app.leads.lead_repository import LeadInsertStats
from app.leads.sync_service import LeadSyncService


class SyncStartupTests(unittest.TestCase):
    def setUp(self):
        self.settings = GoogleSheetsSettings(
            'https://script.google.com/macros/s/test/exec', 'x' * 32,
            0, 3600, 100, 30, False,
        )
        self.gateway = Mock()
        self.repository = Mock()
        self.gateway.get_tail.return_value = GatewayTail(6, 7, 0, 'test')

    def service(self, enabled=False):
        service = LeadSyncService(replace(self.settings, enabled_on_startup=enabled),
                                  self.gateway, self.repository)
        self.addCleanup(service.stop)
        return service

    def test_default_off_does_not_read_or_write(self):
        service = self.service()
        with self.assertLogs('app.leads.sync_service', level='INFO') as logs:
            service.start()
        service.sync_once()
        self.assertFalse(service.state.enabled)
        self.assertIsNone(service.state.next_row)
        self.assertIsNone(service._thread)
        self.assertIn('接收已关闭', logs.output[0])
        self.gateway.get_tail.assert_not_called()
        self.gateway.read_rows.assert_not_called()
        self.repository.append.assert_not_called()

    def test_restart_refreshes_tail_and_duplicate_start_preserves_it(self):
        read = Event()
        def empty(start):
            read.set()
            return GatewayRows(EXPECTED_HEADERS, [], start, start, start - 1, False)
        self.gateway.read_rows.side_effect = empty
        service = self.service(True)
        service.start()
        self.assertTrue(read.wait(2))
        service.start()
        self.assertEqual(self.gateway.get_tail.call_count, 1)
        service.stop()
        read.clear()
        self.gateway.get_tail.return_value = GatewayTail(9, 10, 0, 'test')
        service.start()
        self.assertTrue(read.wait(2))
        service.stop()
        self.assertEqual([c.args[0] for c in self.gateway.read_rows.call_args_list], [7, 10])
        self.repository.append.assert_not_called()

    def test_tail_failure_keeps_receiving_off(self):
        self.gateway.get_tail.side_effect = RuntimeError('offline')
        service = self.service(True)
        with self.assertRaises(RuntimeError):
            service.start()
        self.assertFalse(service.state.enabled)
        self.assertIsNone(service._thread)
        self.assertIsNotNone(service.state.last_error)
        self.gateway.read_rows.assert_not_called()

    def test_stop_waits_for_current_batch_commit(self):
        entered, release, stopped = Event(), Event(), Event()
        row = ['l:test', '2026-09-09T00:00:00Z'] + [''] * 22
        self.gateway.read_rows.return_value = GatewayRows(EXPECTED_HEADERS, [row], 7, 8, 8, True)
        def append(leads):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test did not release batch')
            return LeadInsertStats(1, 0)
        self.repository.append.side_effect = append
        service = self.service(True)
        service.start()
        try:
            self.assertTrue(entered.wait(2))
            worker = Thread(target=lambda: (service.stop(), stopped.set()))
            worker.start()
            self.assertFalse(stopped.wait(0.1))
        finally:
            release.set()
        worker.join(2)
        self.assertTrue(stopped.is_set())
        self.assertEqual(service.state.inserted_count, 1)
        self.assertFalse(service.state.enabled)
        self.assertFalse(service.state.syncing)
        self.assertIsNone(service.state.next_row)
        self.assertEqual(self.gateway.read_rows.call_count, 1)



class AppLifecycleTests(unittest.TestCase):
    def settings(self, enabled):
        from app.settings import Settings, MySQLSettings
        return Settings(GoogleSheetsSettings('https://script.google.com/macros/s/test/exec',
                        'x'*32, 0, 3600, 100, 30, enabled),
                        MySQLSettings('127.0.0.1', 3306, 'test', 'test', 'test'))

    def test_disabled_app_starts_without_external_connections(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import main
        with patch.object(main, 'load_settings', return_value=self.settings(False)), \
             patch.object(main, 'GoogleSheetsGatewayClient') as gateway, \
             patch.object(main.mysql.connector, 'connect') as connect:
            with TestClient(main.app) as http:
                self.assertEqual(http.get('/').status_code, 200)
                self.assertFalse(main.app.state.lead_sync.state.enabled)
                gateway.return_value.get_tail.assert_not_called()
                connect.assert_not_called()
            gateway.return_value.close.assert_called_once()

    def test_enabled_app_releases_gateway_after_worker_stops(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import main
        read = Event()
        gateway = Mock()
        gateway.get_tail.return_value = GatewayTail(6, 7, 0, 'test')
        def empty(start):
            read.set()
            return GatewayRows(EXPECTED_HEADERS, [], start, start, start-1, False)
        gateway.read_rows.side_effect = empty
        with patch.object(main, 'load_settings', return_value=self.settings(True)), \
             patch.object(main, 'GoogleSheetsGatewayClient', return_value=gateway), \
             patch.object(main.mysql.connector, 'connect') as connect:
            with TestClient(main.app):
                self.assertTrue(read.wait(2))
                self.assertTrue(main.app.state.lead_sync.state.enabled)
            self.assertIsNone(main.app.state.lead_sync._thread)
            gateway.close.assert_called_once()
            connect.assert_not_called()

    def test_failed_start_closes_gateway(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import main
        gateway = Mock()
        gateway.get_tail.side_effect = RuntimeError('offline')
        with patch.object(main, 'load_settings', return_value=self.settings(True)), \
             patch.object(main, 'GoogleSheetsGatewayClient', return_value=gateway):
            with self.assertRaises(RuntimeError):
                with TestClient(main.app):
                    pass
            gateway.close.assert_called_once()

    def test_repository_only_skips_duplicate_keys(self):
        from app.leads.lead_repository import LeadRepository
        from app.leads.lead_mapper import map_row
        from mysql.connector.errors import IntegrityError, DataError
        lead = map_row(EXPECTED_HEADERS, ['l:test', '2026-09-09T00:00:00Z']+['']*22)
        conn = Mock()
        conn.cursor.return_value.execute.side_effect = [None, IntegrityError(errno=1062)]
        self.assertEqual(LeadRepository(lambda: conn).append([lead, lead]), LeadInsertStats(1, 1))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()
        conn = Mock()
        conn.cursor.return_value.execute.side_effect = DataError('invalid value')
        with self.assertRaises(DataError):
            LeadRepository(lambda: conn).append([lead])
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
