from urllib.parse import urlencode

from fastapi import HTTPException

from utils.browser import PlaywrightManager

# Category ID mapping for Kleinanzeigen
CATEGORY_IDS = {
    "autoteile-reifen": 223,
    "reifen-felgen": 233,
}


async def get_inserate_klaz(browser_manager: PlaywrightManager,
                            query: str = None,
                            location: str = None,
                            radius: int = None,
                            min_price: int = None,
                            max_price: int = None,
                            page_count: int = 1,
                            category: str = None,
                            commercial_only: bool = False):
    base_url = "https://www.kleinanzeigen.de"

    # Build category path and suffix
    category_path = ""
    category_suffix = ""
    if category and category in CATEGORY_IDS:
        category_path = f"/s-{category}"
        category_suffix = f"/k0c{CATEGORY_IDS[category]}"

    # Build commercial filter path
    commercial_path = "/anbieter:gewerblich" if commercial_only else ""

    # Build the price filter part of the path
    price_path = ""
    if min_price is not None or max_price is not None:
        min_price_str = str(min_price) if min_price is not None else ""
        max_price_str = str(max_price) if max_price is not None else ""
        price_path = f"/preis:{min_price_str}:{max_price_str}"

    # Build search query path (hyphenated, goes before category suffix)
    query_path = ""
    if query:
        query_path = f"/{query.replace(' ', '-').lower()}"

    # Build query parameters for location (still uses query params)
    params = {}
    if location:
        params['locationStr'] = location
    if radius:
        params['radius'] = radius

    # Construct the URL pattern based on whether category is used
    if category and category in CATEGORY_IDS:
        # Category-based URL: /s-{cat}/{commercial}/{page}/{price}/{query}/k0c{id}
        # Order: category > commercial > seite:N > price > query > k0cID
        # Note: page 1 omits /seite:1, subsequent pages use /seite:{page}
        search_path = f"{category_path}{commercial_path}{{page_suffix}}{price_path}{query_path}{category_suffix}"
    else:
        # Legacy URL pattern (no category): {price}/s-seite:{page}?keywords=...
        search_path = f"{price_path}/s-seite:{{page}}"
        if query:
            params['keywords'] = query

    # Construct the full URL
    search_url_template = base_url + search_path + ("?" + urlencode(params) if params else "")

    # Helper to build URL for a specific page
    use_category_pagination = category and category in CATEGORY_IDS
    def build_url(page_num):
        if use_category_pagination:
            # Category URLs: page 1 has no suffix, page 2+ uses /seite:{n}/
            page_suffix = "" if page_num == 1 else f"/seite:{page_num}"
            return search_url_template.format(page_suffix=page_suffix)
        else:
            return search_url_template.format(page=page_num)

    page = await browser_manager.new_context_page()
    try:
        url = build_url(1)
        print(f"[inserate] Category: {category}, Commercial only: {commercial_only}")
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
                    next_url = build_url(i+2)
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
