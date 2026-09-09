from filter_agent import ChatMessage, UnreadBatch


def make_batch() -> UnreadBatch:
    return UnreadBatch(
        customer_name="张三",
        customer_phone="+8613800000000",
        context_messages=[
            ChatMessage(
                message_key="context-1",
                sender="客服",
                direction="out",
                timestamp="10:00, 29/08/2026",
                text="请问您哪里没有信号？",
            )
        ],
        unread_messages=[
            ChatMessage(
                message_key="unread-1",
                sender="张三",
                direction="in",
                timestamp="10:01, 29/08/2026",
                text="地下室没有手机信号",
            )
        ],
    )
