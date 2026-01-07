from playwright.sync_api import sync_playwright

def verify_update_ui():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Mock the API response to force update available
        page.route("**/api/update/check", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"has_update": true, "message": "Update available", "changelog": [{"date": "2023-10-01", "message": "feat: Added AI search"}]}'
        ))

        # 2. Go to home page
        page.goto("http://localhost:5001")

        # 3. Handle Nag Screen (if present)
        # We wait a bit to see if it pops up
        try:
            nag_btn = page.locator("#btnAgree")
            if nag_btn.is_visible(timeout=3000):
                print("Nag screen detected. Clicking 'I agree'...")
                nag_btn.click()
                # Wait for animation
                page.wait_for_timeout(1000)
        except Exception:
            print("No nag screen detected or timed out.")

        # 4. Wait for banner
        print("Waiting for update banner...")
        banner = page.locator("#update-banner")
        banner.wait_for(state="visible", timeout=5000)

        # 5. Click banner to open modal
        print("Clicking update banner...")
        banner.click()

        # 6. Wait for modal
        print("Waiting for update modal...")
        modal = page.locator("#update-modal")
        modal.wait_for(state="visible")

        # 7. Screenshot
        page.screenshot(path="verification/update_ui.png")
        print("Screenshot saved to verification/update_ui.png")

        browser.close()

if __name__ == "__main__":
    verify_update_ui()
