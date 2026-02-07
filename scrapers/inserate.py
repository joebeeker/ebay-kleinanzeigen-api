from urllib.parse import urlencode

from fastapi import HTTPException

from utils.browser import PlaywrightManager


async def get_inserate_klaz(browser_manager: PlaywrightManager,
                            query: str = None,
                            location: str = None,
                            radius: int = None,
                            min_price: int = None,
                            max_price: int = None,
                            page_count: int = 1):
    base_url = "https://www.kleinanzeigen.de"

    # Build the price filter part of the path
    price_path = ""
    if min_price is not None or max_price is not None:
        # Convert prices to strings; if one is None, leave its place empty
        min_price_str = str(min_price) if min_price is not None else ""
        max_price_str = str(max_price) if max_price is not None else ""
        price_path = f"/preis:{min_price_str}:{max_price_str}"

    # Build the search path with price and page information
    search_path = f"{price_path}/s-seite"
    search_path += ":{page}"

    # Build query parameters as before
    params = {}
    if query:
        params['keywords'] = query
    if location:
        params['locationStr'] = location
    if radius:
        params['radius'] = radius

    # Construct the full URL and get it
    search_url = base_url + search_path + ("?" + urlencode(params) if params else "")

    page = await browser_manager.new_context_page()
    try:
        url = search_url.format(page=1)
        print(f"[inserate] Loading page 1: {url}")
        await page.goto(url, timeout=60000)
        print(f"[inserate] Page loaded, waiting for content...")

        # Use domcontentloaded instead of networkidle (more reliable)
        await page.wait_for_load_state("domcontentloaded")

        # Handle cookie consent if present
        try:
            consent_button = await page.query_selector("#gdpr-banner-accept")
            if consent_button:
                print("[inserate] Accepting cookie consent...")
                await consent_button.click()
                await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[inserate] Cookie consent handling: {e}")

        # Wait for ad items to appear
        print("[inserate] Waiting for ad items...")
        try:
            await page.wait_for_selector(".ad-listitem", timeout=10000)
        except Exception as e:
            print(f"[inserate] No ad items found: {e}")
            # Take screenshot for debugging
            page_content = await page.content()
            print(f"[inserate] Page title: {await page.title()}")
            print(f"[inserate] Page content length: {len(page_content)}")

        results = []

        for i in range(page_count):
            page_results = await get_ads(page)
            print(f"[inserate] Page {i+1}: found {len(page_results)} ads")
            results.extend(page_results)

            if i < page_count - 1:
                try:
                    next_url = search_url.format(page=i+2)
                    print(f"[inserate] Loading page {i+2}: {next_url}")
                    await page.goto(next_url, timeout=60000)
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_selector(".ad-listitem", timeout=10000)
                except Exception as e:
                    print(f"[inserate] Failed to load page {i + 2}: {str(e)}")
                    break

        print(f"[inserate] Total results: {len(results)}")
        return results
    except Exception as e:
        print(f"[inserate] ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await browser_manager.close_page(page)


async def get_ads(page):
    try:
        items = await page.query_selector_all(".ad-listitem:not(.is-topad):not(.badge-hint-pro-small-srp)")
        results = []
        for item in items:
            article = await item.query_selector("article")
            if article:
                data_adid = await article.get_attribute("data-adid")
                data_href = await article.get_attribute("data-href")
                # Get title from h2 element
                title_element = await article.query_selector("h2.text-module-begin a.ellipsis")
                title_text = await title_element.inner_text() if title_element else ""
                # Get price and description
                price = await article.query_selector("p.aditem-main--middle--price-shipping--price")
                # strip € and VB and strip whitespace
                price_text = await price.inner_text() if price else ""
                price_text = price_text.replace("€", "").replace("VB", "").replace(".", "").strip()
                description = await article.query_selector("p.aditem-main--middle--description")
                description_text = await description.inner_text() if description else ""
                if data_adid and data_href:
                    data_href = f"https://www.kleinanzeigen.de{data_href}"
                    results.append({"adid": data_adid, "url": data_href, "title": title_text, "price": price_text, "description": description_text})
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
