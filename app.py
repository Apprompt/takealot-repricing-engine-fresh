import os
import json
import time
import random
import hashlib
import logging
import threading
import requests
import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from datetime import datetime

# -----------------------------------------------------
# CONFIGURE LOGGING
# -----------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# -----------------------------------------------------
# REPRICING ENGINE CLASS
# -----------------------------------------------------
class TakealotRepricingEngine:
    def __init__(self):
        self.session = requests.Session()
        self.price_cache = {}
        self.cache_ttl = 3600
        self.last_request_time = 0
        self.min_request_interval = 2.5

        # API credentials from environment
        self.api_key = os.getenv("TAKEALOT_API_KEY")
        self.seller_id = os.getenv("TAKEALOT_SELLER_ID")

        # Load product config
        self.product_config = self._load_product_config()

        logger.info("🚀 Takealot Repricing Engine Initialized")

    # -------------------------------------------------
    # LOAD CSV CONFIG
    # -------------------------------------------------
    def _load_product_config(self):
        try:
            file_path = "products_config.csv"
            logger.info(f"🔍 Loading product config: {file_path}")

            if not os.path.exists(file_path):
                logger.error("❌ products_config.csv not found!")
                return {}

            df = pd.read_csv(file_path)
            expected_cols = {"OfferID", "SellingPrice", "CostPrice"}
            missing = expected_cols - set(df.columns)
            if missing:
                logger.error(f"❌ Missing columns in CSV: {missing}")
                return {}

            config_dict = {
                str(row["OfferID"]): {
                    "selling_price": int(row["SellingPrice"]),
                    "cost_price": int(row["CostPrice"])
                }
                for _, row in df.iterrows()
            }

            logger.info(f"✅ Loaded {len(config_dict)} products from CSV.")
            return config_dict
        except Exception as e:
            logger.error(f"❌ Failed to load product config: {e}")
            return {}

    # -------------------------------------------------
    # PRODUCT CONFIG LOOKUP
    # -------------------------------------------------
    def get_product_thresholds(self, offer_id):
        offer_id = str(offer_id)
        if offer_id in self.product_config:
            cfg = self.product_config[offer_id]
            logger.info(
                f"✅ Found config for {offer_id}: cost R{cfg['cost_price']}, selling R{cfg['selling_price']}"
            )
            return cfg["cost_price"], cfg["selling_price"]
        logger.warning(f"⚠️ No config found for {offer_id}, using fallback R500/R700.")
        return 500, 700

    # -------------------------------------------------
    # COMPETITOR PRICE SCRAPING
    # -------------------------------------------------
    def get_competitor_price(self, offer_id):
        try:
            cached = self._get_cached_price(offer_id)
            if cached:
                logger.info(f"💾 Cached competitor price for {offer_id}: R{cached}")
                return cached

            self._respect_rate_limit()
            competitor_price = self._scrape_takealot_price(offer_id)

            if competitor_price:
                self._cache_price(offer_id, competitor_price)
                logger.info(f"💰 Competitor price for {offer_id}: R{competitor_price}")
                return competitor_price
            else:
                logger.warning(f"⚠️ No competitor price scraped for {offer_id}.")
                return self._get_fallback_price(offer_id)
        except Exception as e:
            logger.error(f"❌ get_competitor_price failed: {e}")
            return self._get_fallback_price(offer_id)

    def _scrape_takealot_price(self, offer_id):
        """Scrape real competitor price from Takealot public page"""
        try:
            url = f"https://www.takealot.com/p/{offer_id}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = self.session.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"⚠️ Failed to fetch {url}: {resp.status_code}")
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            price_tag = soup.find("span", class_="currency")
            if not price_tag:
                logger.warning(f"⚠️ Price tag not found for {offer_id}.")
                return None

            price_text = price_tag.get_text(strip=True).replace("R", "").replace(",", "")
            competitor_price = float(price_text)
            return competitor_price
        except Exception as e:
            logger.error(f"❌ Scraping error for {offer_id}: {e}")
            return None

    # -------------------------------------------------
    # PRICE CALCULATION
    # -------------------------------------------------
    def calculate_optimal_price(self, my_price, competitor_price, offer_id):
        cost_price, selling_price = self.get_product_thresholds(offer_id)
        my_price, competitor_price, cost_price, selling_price = map(
            int, [my_price, competitor_price, cost_price, selling_price]
        )

        logger.info(f"🧮 Calculating price for {offer_id}")
        logger.info(
            f"   My: R{my_price}, Competitor: R{competitor_price}, Cost: R{cost_price}, Sell: R{selling_price}"
        )

        if my_price == competitor_price:
            logger.info("✅ No change needed.")
            return my_price

        if competitor_price < cost_price:
            logger.info(f"🔄 Competitor below cost → revert to selling price R{selling_price}")
            return selling_price

        new_price = max(cost_price, competitor_price - 1)
        logger.info(f"📉 Adjusted to R1 below competitor → R{new_price}")
        return new_price

    # -------------------------------------------------
    # SELLER API UPDATE (REAL)
    # -------------------------------------------------
    def update_price(self, offer_id, new_price):
        """Send real update to Takealot Seller API"""
        try:
            if not self.api_key or not self.seller_id:
                logger.error("❌ Missing TAKEALOT_API_KEY or TAKEALOT_SELLER_ID env vars.")
                return False

            url = f"https://seller-api.takealot.com/v2/offers/{offer_id}/price/"
            payload = {
                "seller_id": int(self.seller_id),
                "offer_id": int(offer_id),
                "selling_price": int(new_price)
            }
            headers = {
                "Authorization": f"Key {self.api_key}",
                "Content-Type": "application/json"
            }

            logger.info(f"📤 Updating price for {offer_id} → R{new_price}")
            resp = self.session.post(url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                logger.info(f"✅ Takealot price updated successfully: {resp.text}")
                return True
            else:
                logger.error(f"❌ Update failed ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Exception in update_price: {e}")
            return False

    # -------------------------------------------------
    # INTERNAL HELPERS
    # -------------------------------------------------
    def _get_cached_price(self, offer_id):
        cached = self.price_cache.get(offer_id)
        if cached and time.time() - cached["timestamp"] < self.cache_ttl:
            return cached["price"]
        return None

    def _cache_price(self, offer_id, price):
        self.price_cache[offer_id] = {"price": price, "timestamp": time.time()}

    def _respect_rate_limit(self):
        delta = time.time() - self.last_request_time
        if delta < self.min_request_interval:
            time.sleep(self.min_request_interval - delta + random.uniform(0.5, 1.5))
        self.last_request_time = time.time()

    def _get_fallback_price(self, offer_id):
        h = hashlib.md5(str(offer_id).encode())
        fallback = 500 + (int(h.hexdigest()[:8], 16) % 100)
        logger.warning(f"🔄 Using fallback price R{fallback} for {offer_id}")
        return float(fallback)


# -----------------------------------------------------
# INITIALIZE ENGINE
# -----------------------------------------------------
engine = TakealotRepricingEngine()

# -----------------------------------------------------
# FLASK ROUTES
# -----------------------------------------------------
@app.route("/")
def home():
    return jsonify({
        "status": "healthy",
        "service": "Takealot Repricing Engine",
        "version": "2.1.0",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Takealot Repricing Engine",
        "version": "2.1.0"
    })

# -----------------------------------------------------
# ASYNC WEBHOOK HANDLER
# -----------------------------------------------------
@app.route("/webhook/price-change", methods=["POST"])
def handle_price_change():
    try:
        data = request.get_json()
        logger.info(f"📥 Webhook received: {data}")

        # Start background thread for processing
        threading.Thread(target=process_price_change, args=(data,)).start()

        # Respond immediately to Takealot
        return jsonify({
            "status": "received",
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


def process_price_change(data):
    """Background thread: performs scraping + logic + API update"""
    try:
        offer_id = data.get("offer_id")
        if not offer_id:
            logger.error("❌ Missing offer_id in webhook data")
            return

        # Parse current price
        values_changed = data.get("values_changed", "{}")
        try:
            values_dict = json.loads(values_changed)
            my_current_price = values_dict.get("selling_price", {}).get("new_value", 0)
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse values_changed: {e}")
            my_current_price = 0

        logger.info(f"💰 Processing offer {offer_id}: current price R{my_current_price}")

        # Run repricing flow
        competitor_price = engine.get_competitor_price(offer_id)
        optimal_price = engine.calculate_optimal_price(my_current_price, competitor_price, offer_id)

        if int(optimal_price) != int(my_current_price):
            success = engine.update_price(offer_id, optimal_price)
            logger.info(f"✅ Offer {offer_id} updated to R{optimal_price} (success={success})")
        else:
            logger.info(f"⏸️ Offer {offer_id} already optimal (R{my_current_price})")

    except Exception as e:
        logger.error(f"❌ Error in process_price_change: {e}")


# -----------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Starting Takealot Repricing Engine on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
