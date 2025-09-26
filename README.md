# Toutiao Profile Scraper

This repository includes a Playwright-based utility for collecting the latest items from a specific Toutiao user profile.

## Usage

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Run the scraper (collects 100 items by default):
   ```bash
   python scripts/toutiao_profile_scraper.py
   ```
3. Results will be saved to `data/toutiao_profile_items.json` and `data/toutiao_profile_items.csv`.

The current configuration targets the profile requested in the project brief. You can adjust the `PROFILE_TOKEN` constant in `scripts/toutiao_profile_scraper.py` if you need to scrape a different user.
