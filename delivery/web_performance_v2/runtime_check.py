#!/usr/bin/env python3
"""只验证目标现有中间件，不导入应用、不读取配置、不连接网络或数据库。"""
import asyncio
import gzip


async def check():
    from starlette.middleware.gzip import GZipMiddleware

    payload = b'SYNTHETIC ' * 1000
    for content_type, compressed in [('text/html', True), ('text/event-stream', False)]:
        sent = []

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', content_type.encode())]})
            await send({'type': 'http.response.body', 'body': payload})

        async def receive():
            return {'type': 'http.request', 'body': b''}

        async def send(message):
            sent.append(message)

        await GZipMiddleware(app, minimum_size=1000)(
            {'type': 'http', 'headers': [(b'accept-encoding', b'gzip')]}, receive, send)
        headers = dict(sent[0]['headers'])
        wire = b''.join(m.get('body', b'') for m in sent[1:])
        assert (headers.get(b'content-encoding') == b'gzip') is compressed
        assert (gzip.decompress(wire) if compressed else wire) == payload


if __name__ == '__main__':
    try:
        asyncio.run(check())
    except Exception:
        raise SystemExit('GZIP_SSE_RUNTIME=FAIL') from None
    print('GZIP_SSE_RUNTIME=PASS')
