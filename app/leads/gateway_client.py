"""Apps Script 网关客户端：只负责 HTTP 读取和响应校验。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import httpx
from app.settings import GoogleSheetsSettings

class GatewayError(RuntimeError): pass
@dataclass(frozen=True, slots=True)
class GatewayTail:
    last_row: int
    next_row: int
    gid: int
    sheet_name: str
@dataclass(frozen=True, slots=True)
class GatewayRows:
    headers: list[str]
    values: list[list[Any]]
    start_row: int
    next_row: int
    last_row: int
    has_more: bool
class GoogleSheetsGatewayClient:
    def __init__(self, settings: GoogleSheetsSettings, *, client: httpx.Client | None = None):
        self.settings=settings; self._client=client or httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=True); self._owns_client=client is None
    def close(self):
        if self._owns_client: self._client.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def _post(self, action: str, **params):
        try:
            response=self._client.post(self.settings.apps_script_url,json={"token":self.settings.apps_script_token,"gid":self.settings.gid,"action":action,**params}); response.raise_for_status(); data=response.json()
        except httpx.TimeoutException as exc: raise GatewayError("Apps Script 网关请求超时") from exc
        except httpx.HTTPStatusError as exc: raise GatewayError(f"Apps Script 网关 HTTP 错误：{exc.response.status_code}") from exc
        except httpx.HTTPError as exc: raise GatewayError("Apps Script 网关网络请求失败") from exc
        except ValueError as exc: raise GatewayError("Apps Script 网关返回的不是有效 JSON") from exc
        if not isinstance(data,dict): raise GatewayError("网关响应必须是 JSON 对象")
        if data.get("ok") is not True:
            error=data.get("error","unknown_error")
            raise GatewayError("Apps Script 网关认证失败，请检查访问令牌" if error=="unauthorized" else f"Apps Script 网关拒绝请求：{error}")
        return data
    def get_tail(self):
        d=self._post("tail"); return GatewayTail(_pos(d,"last_row"),_pos(d,"next_row"),_nonneg(d,"gid"),_text(d,"sheet_name"))
    def read_rows(self,start_row:int,limit:int|None=None):
        if not isinstance(start_row,int) or isinstance(start_row,bool) or start_row<=1: raise ValueError("start_row 必须是大于 1 的整数")
        n=self.settings.batch_size if limit is None else limit
        if not isinstance(n,int) or isinstance(n,bool) or not 1<=n<=500: raise ValueError("limit 必须是 1～500 的整数")
        d=self._post("read",start_row=start_row,limit=n); h=d.get("headers"); v=d.get("values")
        if not isinstance(h,list) or not all(isinstance(x,str) for x in h): raise GatewayError("网关响应缺少有效 headers")
        if not isinstance(v,list) or not all(isinstance(x,list) for x in v): raise GatewayError("网关响应缺少有效 values")
        if not isinstance(d.get("has_more"),bool): raise GatewayError("网关响应缺少有效 has_more")
        return GatewayRows(h,v,_pos(d,"start_row"),_pos(d,"next_row"),_pos(d,"last_row"),d["has_more"])
def _text(d,n):
    if not isinstance(d.get(n),str) or not d[n].strip(): raise GatewayError(f"网关响应字段 {n} 无效")
    return d[n]
def _pos(d,n):
    if not isinstance(d.get(n),int) or isinstance(d[n],bool) or d[n]<1: raise GatewayError(f"网关响应字段 {n} 无效")
    return d[n]
def _nonneg(d,n):
    if not isinstance(d.get(n),int) or isinstance(d[n],bool) or d[n]<0: raise GatewayError(f"网关响应字段 {n} 无效")
    return d[n]
