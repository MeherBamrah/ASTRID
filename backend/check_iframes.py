import asyncio
from playwright.async_api import async_playwright

async def main():
    p = await async_playwright().start()
    b = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
    ctx = b.contexts[0]
    for page in ctx.pages:
        if 'instructure.com' in page.url:
            print('Page URL:', page.url)
            iframes = await page.query_selector_all('iframe')
            print(f'Total iframe elements: {len(iframes)}')
            for i, ifr in enumerate(iframes):
                src = await ifr.get_attribute('src')
                name = await ifr.get_attribute('name')
                id_attr = await ifr.get_attribute('id')
                cf = await ifr.content_frame()
                print(f'--- Iframe {i} (id={id_attr}, name={name}, src={src}) ---')
                if cf:
                    print('  CF URL:', cf.url)
                    try:
                        text = await cf.inner_text('body', timeout=2000)
                        print(f'  CF Text:\n{text[:500]}')
                        # Check buttons in this frame!
                        btns = await cf.locator('button, [role="button"], a, input[type="button"], input[type="submit"]').all()
                        print(f'  Found {len(btns)} buttons in frame:')
                        for btn in btns:
                            t = (await btn.inner_text()).strip()
                            v = await btn.get_attribute('value')
                            aria = await btn.get_attribute('aria-label')
                            print(f'    Button: text="{t}" value="{v}" aria="{aria}"')
                    except Exception as e:
                        print('  Error reading CF:', e)

                    # Also check nested iframes inside this frame!
                    nested = await cf.query_selector_all('iframe')
                    print(f'  Nested iframes inside CF: {len(nested)}')
                    for j, n_ifr in enumerate(nested):
                        n_cf = await n_ifr.content_frame()
                        if n_cf:
                            print(f'    Nested CF {j} URL: {n_cf.url}')
                            try:
                                n_text = await n_cf.inner_text('body', timeout=2000)
                                print(f'    Nested CF Text:\n{n_text[:400]}')
                            except Exception as e:
                                print('    Nested CF error:', e)
    await b.close()
    await p.stop()

if __name__ == '__main__':
    asyncio.run(main())
