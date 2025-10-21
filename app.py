import os
import logging
import json
from flask import Flask, request, jsonify
from datetime import datetime
import requests
import time
import random
import hashlib
import pandas as pd
import sqlite3
import threading
import re

print("🔍 DEBUG: Checking environment variables on startup...")
print(f"TAKEALOT_API_KEY exists: {bool(os.getenv('TAKEALOT_API_KEY'))}")
print(f"TAKEALOT_API_SECRET exists: {bool(os.getenv('TAKEALOT_API_SECRET'))}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Constants
PRICE_FRESHNESS_SECONDS = 3600  # 1 hour
MONITORING_INTERVAL_MINUTES = 30
MIN_REQUEST_INTERVAL = 3.0

class PriceMonitor:
    def __init__(self):
        self.db_file = "price_monitor.db"
        self._init_database()
        self.monitoring_thread = None
        self.is_monitoring = False
        
    def _init_database(self):
        """Initialize SQLite database for price storage"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS competitor_prices (
                        offer_id TEXT PRIMARY KEY,
                        competitor_price REAL,
                        last_updated TIMESTAMP,
                        source TEXT
                    )
                ''')
                conn.commit()
            logger.info("✅ Price monitoring database initialized")
        except Exception as e:
            logger.error(f"❌ Database initialization failed: {e}")
    
    def store_competitor_price(self, offer_id, price, source="scraping"):
        """Store competitor price in database"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO competitor_prices 
                    (offer_id, competitor_price, last_updated, source)
                    VALUES (?, ?, ?, ?)
                ''', (str(offer_id), price, datetime.now().isoformat(), source))
                conn.commit()
            logger.info(f"💾 Stored competitor price for {offer_id}: R{price}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to store price: {e}")
            return False
    
    def get_competitor_price(self, offer_id):
        """Get stored competitor price (INSTANT)"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT competitor_price, last_updated, source 
                    FROM competitor_prices 
                    WHERE offer_id = ?
                ''', (str(offer_id),))
                result = cursor.fetchone()
            
            if result:
                price, last_updated, source = result
                # Check if data is fresh (less than 1 hour old)
                try:
                    last_time = datetime.fromisoformat(last_updated)
                    time_diff = (datetime.now() - last_time).total_seconds()
                    
                    if time_diff < PRICE_FRESHNESS_SECONDS:
                        logger.info(f"💾 Using FRESH stored competitor price for {offer_id}: R{price} (from {source})")
                        return price
                    else:
                        logger.info(f"🔄 Stored price too old ({int(time_diff/60)} minutes)")
                        return None
                except Exception as e:
                    logger.error(f"❌ Error parsing timestamp: {e}")
                    return None
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get stored price: {e}")
            return None

    def start_monitoring(self, product_list, interval_minutes=MONITORING_INTERVAL_MINUTES):
        """Start background monitoring of all products"""
        if self.is_monitoring:
            logger.info("📊 Monitoring already running")
            return
        
        self.is_monitoring = True
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(product_list, interval_minutes),
            daemon=True
        )
        self.monitoring_thread.start()
        logger.info(f"🚀 Started background monitoring for {len(product_list)} products")
    
    def _monitoring_loop(self, product_list, interval_minutes):
        """Background loop to monitor all products"""
        while self.is_monitoring:
            try:
                logger.info(f"🔄 Monitoring cycle started for {len(product_list)} products")
                
                for offer_id in product_list:
                    if not self.is_monitoring:
                        break
                    try:
                        # Use direct scraping instead of engine method to avoid circular reference
                        competitor_price = self._direct_scrape_price(offer_id)
                        if competitor_price and competitor_price > 0:
                            self.store_competitor_price(offer_id, competitor_price, "background_monitor")
                        # Be nice to Takealot's servers
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"❌ Monitoring failed for {offer_id}: {e}")
                        time.sleep(5)
                
                if self.is_monitoring:
                    logger.info(f"⏰ Monitoring cycle completed. Sleeping for {interval_minutes} minutes")
                    time.sleep(interval_minutes * 60)
                    
            except Exception as e:
                logger.error(f"❌ Monitoring loop error: {e}")
                if self.is_monitoring:
                    time.sleep(60)

    def _extract_plid_from_url(self, product_url):
        """Extract PLID from Takealot shortened URL"""
        try:
            # Handle format: https://www.takealot.com/x/PLID90609721
            plid_match = re.search(r'/x/PLID(\d+)', product_url)
            if plid_match:
                plid = f"PLID{plid_match.group(1)}"
                logger.debug(f"✅ Extracted PLID from shortened URL: {plid}")
                return plid
            
            # Fallback: look for any PLID in URL
            plid_match = re.search(r'/PLID(\d+)', product_url)
            if plid_match:
                plid = f"PLID{plid_match.group(1)}"
                logger.debug(f"✅ Extracted PLID from full URL: {plid}")
                return plid
            
            logger.warning(f"⚠️ Could not extract PLID from URL: {product_url}")
            return None
            
        except Exception as e:
            logger.error(f"❌ URL parsing failed for {product_url}: {e}")
            return None

    def _direct_scrape_price(self, offer_id):
        """Direct price scraping for monitoring - USING PLID FROM URL"""
        try:
            # Get product config to find PLID from URL
            product_info = engine.product_config.get(offer_id, {})
            plid = product_info.get("plid")
            
            if not plid:
                logger.warning(f"⚠️ No PLID mapping for product {offer_id}")
                return None
                
            logger.info(f"🔍 Monitoring scraping {offer_id} → {plid}")
            
            # Use the PLID in the API call with PROPER headers
            api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Referer": f"https://www.takealot.com/{plid.lower()}",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors", 
                "Sec-Fetch-Site": "same-site",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            
            response = requests.get(api_url, headers=headers, timeout=15)
            logger.info(f"📊 API response for {plid}: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                product = data.get("product", {})
                
                # Your existing price extraction logic...
                price_candidates = []
                
                # Buybox price
                buybox = product.get("buybox", {})
                if buybox:
                    buybox_price = buybox.get("price")
                    if buybox_price and buybox_price > 0:
                        price_rand = buybox_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Found price for {plid}: R{price_rand}")
                
                # Core price
                core_price = product.get("core", {}).get("price") or product.get("price")
                if core_price:
                    if isinstance(core_price, dict):
                        selling_price = core_price.get("selling_price") or core_price.get("amount")
                        if selling_price and selling_price > 0:
                            price_rand = selling_price / 100.0
                            price_candidates.append(price_rand)
                            logger.info(f"💰 Found core price for {plid}: R{price_rand}")
                    else:
                        price_rand = core_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Found direct price for {plid}: R{price_rand}")
                
                if price_candidates:
                    lowest_price = min(price_candidates)
                    logger.info(f"🏆 Monitoring SUCCESS for {plid}: R{lowest_price}")
                    return lowest_price
                
                logger.warning(f"⚠️ No prices found for {plid}")
                return None
            else:
                logger.error(f"❌ API {response.status_code} for {plid}")
                return None
                
        except Exception as e:
            logger.error(f"💥 Scrape failed for {offer_id}: {e}")
            return None

    
    def stop_monitoring(self):
        """Stop background monitoring"""
        self.is_monitoring = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=10)
        logger.info("🛑 Background monitoring stopped")

class TakealotRepricingEngine:
    def __init__(self):
        self.session = requests.Session()
        self.price_cache = {}
        self.cache_ttl = 3600
        self.last_request_time = 0
        self.min_request_interval = MIN_REQUEST_INTERVAL

        # Load product configurations
        self.product_config = self._load_product_config()
        
        # Initialize price monitor
        self.price_monitor = PriceMonitor()
        
        logger.info("🚀 Takealot Repricing Engine with PROACTIVE MONITORING Initialized")

    def _load_product_config(self):
    """Load product config with new 4-column format - ENHANCED DEBUG VERSION"""
    try:
        current_dir = os.getcwd()
        logger.info(f"🔍 DEBUG: Current working directory: {current_dir}")

        file_path = 'products_config.csv'
        logger.info(f"🔍 Looking for: {file_path}")

        if os.path.exists(file_path):
            logger.info("✅ CSV file exists, attempting to read...")
            
            # Read CSV with error handling
            try:
                df = pd.read_csv(file_path)
                logger.info(f"✅ CSV read successfully, shape: {df.shape}")
            except Exception as e:
                logger.error(f"❌ Failed to read CSV: {e}")
                return {}
            
            # ✅ Check for required columns in new format
            expected_cols = {"offer_id", "product_url", "min_price", "max_price"}
            actual_cols = set(df.columns)
            
            logger.info(f"📋 Actual columns: {actual_cols}")
            logger.info(f"📋 Expected columns: {expected_cols}")
            
            if expected_cols != actual_cols:
                logger.error(f"❌ Column mismatch! Expected: {expected_cols}, Got: {actual_cols}")
                return {}
            
            logger.info(f"✅ Column check passed. Loaded CSV with {len(df)} rows")
            
            # Check for empty DataFrame
            if len(df) == 0:
                logger.error("❌ CSV is empty!")
                return {}

            config_dict = {}
            success_count = 0
            error_count = 0
            
            for index, row in df.iterrows():
                try:
                    offer_id = str(row["offer_id"])
                    product_url = row["product_url"]
                    
                    # Basic validation
                    if pd.isna(offer_id) or pd.isna(product_url):
                        logger.warning(f"⚠️ Row {index}: Missing offer_id or product_url")
                        error_count += 1
                        continue
                    
                    # Extract PLID from URL
                    plid = self.price_monitor._extract_plid_from_url(product_url)
                    
                    config_dict[offer_id] = {
                        "min_price": float(row["min_price"]),
                        "max_price": float(row["max_price"]),
                        "product_url": product_url,
                        "plid": plid
                    }
                    success_count += 1
                    
                    # Log first few successes
                    if success_count <= 3:
                        logger.info(f"✅ Loaded product {success_count}: {offer_id} → {plid}")
                        
                except Exception as e:
                    error_count += 1
                    if error_count <= 3:  # Log first few errors
                        logger.error(f"❌ Error loading row {index}: {e}")
                        logger.error(f"❌ Row data: {row.to_dict()}")

            logger.info(f"🎉 FINAL RESULT: Successfully loaded {success_count} products, {error_count} errors")
            
            if success_count == 0:
                logger.error("❌ CRITICAL: No products were loaded successfully!")
                
            return config_dict
        else:
            logger.error("❌ CRITICAL: products_config.csv NOT FOUND in deployment!")
            return {}
    except Exception as e:
        logger.error(f"❌ CRITICAL ERROR in product loading: {e}")
        import traceback
        logger.error(f"❌ Full traceback: {traceback.format_exc()}")
        return {}

    def start_background_monitoring(self):
        """Start monitoring all configured products"""
        product_list = list(self.product_config.keys())
        if product_list:
            self.price_monitor.start_monitoring(product_list, interval_minutes=30)
        else:
            logger.warning("⚠️ No products configured for monitoring")

    def get_product_thresholds(self, offer_id):
        """Get min_price and max_price for specific product - UPDATED"""
        # Convert to string for lookup (since CSV keys are strings)
        offer_id_str = str(offer_id)

        if offer_id_str in self.product_config:
            config = self.product_config[offer_id_str]
            logger.info(f"✅ Found config for {offer_id_str}: min R{config.get('min_price')}, max R{config.get('max_price')}")
            return config.get('min_price'), config.get('max_price')
        else:
            logger.warning(f"⚠️ No configuration found for '{offer_id_str}' - using fallback R500/R700")
            return 500, 700  # Fallback values

    def get_competitor_price_instant(self, offer_id):
        """INSTANT competitor price lookup from database"""
        # Try to get pre-scraped price first (INSTANT)
        stored_price = self.price_monitor.get_competitor_price(offer_id)
        if stored_price is not None:
            return stored_price, 'proactive_monitoring'
        
        # Fallback to real-time scraping (SLOW)
        logger.info("🔄 No stored price available, falling back to real-time scraping")
        real_time_price = self.get_competitor_price(offer_id)
        return real_time_price, 'real_time_scraping'

    def get_competitor_price(self, offer_id):
        """Get competitor price - comprehensive strategy with real scraping and monitoring fallback"""
        try:
            # 1. First try monitoring database (background monitoring)
            stored_price = self.price_monitor.get_competitor_price(offer_id)
            if stored_price is not None:
                logger.info(f"💾 Using MONITORED price for {offer_id}: R{stored_price}")
                return stored_price
            
            # 2. Check cache second
            cached_price = self._get_cached_price(offer_id)
            if cached_price is not None:
                logger.info(f"💾 Using CACHED price for {offer_id}: R{cached_price}")
                return cached_price
            
            # 3. Try REAL scraping third
            logger.info(f"🎯 Attempting REAL competitor price scraping for {offer_id}")
            real_price = self.get_real_competitor_price(offer_id)
            
            # If real scraping returned a valid price, use it
            if real_price and real_price > 0:
                self._cache_price(offer_id, real_price)
                return real_price
            elif real_price == "we_own_buybox":
                # Special case: we own the buybox
                return "we_own_buybox"
            else:
                # 4. Final fallback to simulated scraping
                logger.info("🔄 Real scraping failed, using simulated data")
                simulated_price = self._simulate_scraping(offer_id)
                self._cache_price(offer_id, simulated_price)
                return simulated_price
            
        except Exception as e:
            logger.error(f"❌ All competitor price methods failed: {e}")
            return self._get_fallback_price(offer_id)

    def get_real_competitor_price(self, offer_id):
        """Fetch ACTUAL competitor price from Takealot - USING PLID FROM URL"""
        try:
            self._respect_rate_limit()
            
            # Get PLID from product config (extracted from URL)
            product_info = self.product_config.get(str(offer_id), {})
            plid = product_info.get("plid")
            
            if not plid:
                logger.warning(f"⚠️ No PLID available for {offer_id}")
                return None
            
            # Use the PLID from URL in API call
            api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": f"https://www.takealot.com/{plid.lower()}",
            }
            
            logger.info(f"🌐 Fetching API with PLID from URL: {api_url}")
            response = self.session.get(api_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"✅ API response received")
                
                product = data.get("product", {})
                
                # 🎯 CRITICAL: Try multiple price locations in current API structure
                price_candidates = []
                
                # Method 1: Core price data (most common)
                core_price = product.get("core", {}).get("price") or product.get("price")
                if core_price:
                    # Handle both direct price objects and nested structures
                    if isinstance(core_price, dict):
                        selling_price = core_price.get("selling_price") or core_price.get("amount")
                        if selling_price and selling_price > 0:
                            price_rand = selling_price / 100.0
                            price_candidates.append(price_rand)
                            logger.info(f"💰 Core price found: R{price_rand}")
                    else:
                        # Direct price value
                        price_rand = core_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Direct core price: R{price_rand}")
                
                # Method 2: Buybox data (your main requirement)
                buybox = product.get("buybox", {}) or product.get("purchase_box", {})
                if buybox:
                    buybox_price = buybox.get("price") or buybox.get("current_price")
                    if buybox_price and buybox_price > 0:
                        price_rand = buybox_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Buybox price: R{price_rand}")
                    
                    # 🎯 CHECK BUYBOX WINNER (CRITICAL FOR YOUR LOGIC)
                    buybox_winner = buybox.get("seller_name") or buybox.get("seller_id")
                    if buybox_winner:
                        logger.info(f"🏆 Buybox winner: {buybox_winner}")
                        # Check if WE are the buybox winner
                        if "allbats" in str(buybox_winner).lower() or "29844311" in str(buybox_winner):
                            logger.info("🎉 WE ARE THE BUYBOX WINNER - no adjustment needed")
                            # Return a special value to indicate we own the buybox
                            return "we_own_buybox"
                
                # Method 3: Product variants
                variants = product.get("variants", [])
                for variant in variants:
                    variant_price = variant.get("price") or variant.get("selling_price")
                    if variant_price and variant_price > 0:
                        price_rand = variant_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Variant price: R{price_rand}")
                
                # Method 4: Direct product price fields
                direct_price_fields = ["selling_price", "current_price", "price", "amount"]
                for field in direct_price_fields:
                    price_val = product.get(field)
                    if price_val and price_val > 0:
                        price_rand = price_val / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 {field} price: R{price_rand}")
                
                if price_candidates:
                    lowest_price = min(price_candidates)
                    logger.info(f"🏆 Selected competitor price: R{lowest_price}")
                    return lowest_price
                else:
                    logger.warning("❌ No prices found in API response")
                    # Log the actual API structure for debugging
                    logger.info(f"🔍 API structure keys: {list(product.keys())}")
                    return None
                    
            else:
                logger.error(f"❌ API returned status: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Real scraping failed: {e}")
            import traceback
            logger.error(f"❌ Stack trace: {traceback.format_exc()}")
            return None

    def calculate_optimal_price(self, my_price, competitor_price, offer_id):
        """YOUR EXACT BUSINESS LOGIC with buybox detection"""
        # Get thresholds for THIS specific product
        min_price, max_price = self.get_product_thresholds(offer_id)
        
        # Convert to integers (whole numbers) for Takealot
        my_price = int(my_price)
        min_price = int(min_price)
        max_price = int(max_price)
        
        logger.info(f"🧮 Calculating price for {offer_id}")
        logger.info(f"   My price: R{my_price}, Min: R{min_price}, Max: R{max_price}")
        
        # 🎯 CRITICAL: Check if we own the buybox
        if competitor_price == "we_own_buybox":
            logger.info("🎉 WE OWN THE BUYBOX - no price adjustment needed")
            return my_price  # Keep current price
        
        # Convert competitor price if it's a number
        competitor_price = int(competitor_price) if competitor_price and competitor_price != "we_own_buybox" else None
        
        if not competitor_price or competitor_price <= 0:
            logger.warning("⚠️ No valid competitor price - using max price")
            return max_price
        
        logger.info(f"   Competitor buybox price: R{competitor_price}")
        
        # RULE 1: If competitor below THIS PRODUCT'S min price, revert to THIS PRODUCT'S max price
        if competitor_price < min_price:
            logger.info(f"   🔄 REVERT: Competitor R{competitor_price} below min R{min_price} → R{max_price}")
            return max_price
        
        # RULE 2: Always be R1 below competitor (whole numbers)
        new_price = competitor_price - 1
        
        # Safety check: don't go below min price
        if new_price < min_price:
            logger.info(f"   ⚠️ ADJUSTMENT: R{new_price} below min R{min_price} → R{max_price}")
            return max_price
        
        # Safety check: don't go above max price
        if new_price > max_price:
            logger.info(f"   ⚠️ ADJUSTMENT: R{new_price} above max R{max_price} → R{max_price}")
            return max_price
        
        logger.info(f"   📉 ADJUST: R1 below competitor R{competitor_price} → R{new_price}")
        return new_price

    def update_price(self, offer_id, new_price):
        """Update price on Takealot using BARCODE identifier"""
        try:
            api_key = os.getenv('TAKEALOT_API_KEY')
            BASE_URL = "https://seller-api.takealot.com"
            
            # Use BARCODE identifier - you need to replace this with actual barcode from your products
            # For testing, using one of your existing product barcodes
            barcode = "MPTAL00552087"  # Replace with actual barcode from your product
            
            endpoint = f"{BASE_URL}/v2/offers/offer?identifier=BARCODE{barcode}"
            
            headers = {
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "selling_price": int(new_price)
            }
            
            logger.info(f"🔑 Updating barcode: {barcode}")
            logger.info(f"🌐 Endpoint: {endpoint}")
            
            response = self.session.patch(endpoint, json=payload, headers=headers, timeout=30)
            
            logger.info(f"📥 Response Status: {response.status_code}")
            logger.info(f"📥 Response Text: {response.text}")
            
            if response.status_code == 200:
                logger.info(f"✅ SUCCESS: Updated barcode {barcode} to R{new_price}")
                return True
            else:
                logger.error(f"❌ API update failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Price update failed: {e}")
            return False

    def update_price_with_retry(self, offer_id, new_price, max_retries=3):
        """Update price with retry logic for reliability"""
        for attempt in range(max_retries):
            try:
                success = self.update_price(offer_id, new_price)
                if success:
                    return True
                else:
                    logger.warning(f"🔄 Update attempt {attempt + 1} failed, retrying...")
                    time.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                logger.error(f"❌ Update attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        
        logger.error(f"❌ All {max_retries} update attempts failed for {offer_id}")
        return False

    def _get_cached_price(self, offer_id):
        if offer_id in self.price_cache:
            cached_data = self.price_cache[offer_id]
            if time.time() - cached_data['timestamp'] < self.cache_ttl:
                return cached_data['price']
        return None

    def _cache_price(self, offer_id, price):
        self.price_cache[offer_id] = {
            'price': price,
            'timestamp': time.time()
        }

    def _respect_rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last + random.uniform(0.5, 1.5)
            logger.info(f"⏳ Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()

    def _simulate_scraping(self, offer_id):
        """Fallback: Generate random prices when real scraping fails"""
        time.sleep(1)  # Simulate scraping delay
        # Convert offer_id to string to handle both string and integer IDs
        offer_id_str = str(offer_id)
        hash_obj = hashlib.md5(offer_id_str.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        base_price = 450 + (hash_int % 200)  # Prices between 450-650 (WHOLE NUMBERS)
        logger.info(f"🔄 Using simulated price: R{base_price}")
        return float(base_price)

    def _get_fallback_price(self, offer_id):
        # Convert offer_id to string
        offer_id_str = str(offer_id)
        hash_obj = hashlib.md5(offer_id_str.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        fallback_price = 500 + (hash_int % 100)
        logger.warning(f"🔄 Using fallback price: R{fallback_price}")
        return float(fallback_price)

# Initialize the engine
engine = TakealotRepricingEngine()

# Start background monitoring when app starts (using modern Flask approach)
@app.before_request
def start_background_services():
    if not hasattr(app, 'background_services_started'):
        # Start monitoring in a separate thread to avoid blocking
        def start_monitoring():
            time.sleep(5)  # Wait a bit for app to fully start
            engine.start_background_monitoring()
        
        monitoring_thread = threading.Thread(target=start_monitoring, daemon=True)
        monitoring_thread.start()
        app.background_services_started = True

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Takealot Repricing Engine with Proactive Monitoring',
        'version': '2.0.0',
        'timestamp': datetime.now().isoformat(),
        'environment': os.getenv('RAILWAY_ENVIRONMENT', 'development'),
        'features': 'PROACTIVE MONITORING + Instant Webhook Responses'
    })

@app.route('/webhook/price-change', methods=['POST'])
def handle_price_change():
    """Main webhook endpoint - WITH SECURITY VERIFICATION & INSTANT PRICING"""
    try:
        # Verify webhook signature if secret is provided
        webhook_secret = os.getenv('WEBHOOK_SECRET')
        if webhook_secret:
            signature = request.headers.get('X-Takealot-Signature')
            if not signature:
                logger.warning("⚠️ Missing webhook signature")
                return jsonify({'error': 'Missing signature'}), 401
            
            # Verify the signature (you may need to adjust this based on Takealot's method)
            import hmac
            import hashlib
            
            # Calculate expected signature
            expected_signature = hmac.new(
                webhook_secret.encode(),
                request.get_data(),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                logger.warning("❌ Invalid webhook signature")
                return jsonify({'error': 'Invalid signature'}), 401

        # Continue with webhook processing
        webhook_data = request.get_json()
        logger.info(f"📥 Webhook received: {webhook_data}")
        
        # 🚨 DEBUG: Log ALL webhook fields to see available data
        logger.info(f"🔍 WEBHOOK ALL KEYS: {list(webhook_data.keys())}")
        
        offer_id = webhook_data.get('offer_id')
        
        # Extract YOUR current price from values_changed
        values_changed = webhook_data.get('values_changed', '{}')
        my_current_price = 0
        
        try:
            if isinstance(values_changed, str):
                values_dict = json.loads(values_changed)
            else:
                values_dict = values_changed
                
            # Get your NEW selling price from the webhook
            my_current_price = values_dict.get('selling_price', {}).get('new_value', 0)
            if not my_current_price:
                # Try alternative field names
                my_current_price = values_dict.get('current_price') or values_dict.get('price') or 0
        except Exception as e:
            logger.error(f"❌ Failed to extract my price: {e}")
            my_current_price = 0
        
        logger.info(f"💰 Extracted - Offer: {offer_id}, My Price: R{my_current_price}")
        
        if not offer_id:
            return jsonify({'error': 'Missing offer_id'}), 400
        
        # 🎯 INSTANT competitor price lookup
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        
        logger.info(f"🎉 USING {source.upper()} COMPETITOR DATA: R{competitor_price}")
        
        # Calculate optimal price using your business logic
        optimal_price = engine.calculate_optimal_price(my_current_price, competitor_price, offer_id)
        
        # Determine if update is needed
        needs_update = optimal_price != my_current_price
        
        if needs_update:
            update_success = engine.update_price_with_retry(offer_id, optimal_price)
            status = 'updated' if update_success else 'update_failed'
        else:
            update_success = False
            status = 'no_change'
        
        response = {
            'status': status,
            'offer_id': offer_id,
            'your_current_price': int(my_current_price),
            'competitor_price': int(competitor_price) if competitor_price and competitor_price != "we_own_buybox" else "we_own_buybox",
            'competitor_source': source,
            'calculated_price': optimal_price,
            'price_updated': update_success,
            'business_rule': describe_business_rule(my_current_price, competitor_price, optimal_price),
            'response_time': 'INSTANT' if source == 'proactive_monitoring' else 'SLOW',
            'timestamp': datetime.now().isoformat(),
            'webhook_fields_found': list(webhook_data.keys())
        }
        
        logger.info(f"📤 Webhook response: {response}")
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        import traceback
        logger.error(f"❌ Stack trace: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/test/<offer_id>')
def test_endpoint(offer_id):
    """Test endpoint for manual testing"""
    try:
        test_price = 500  # Whole number now
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        
        # Handle buybox ownership case
        if competitor_price == "we_own_buybox":
            optimal_price = test_price  # No change if we own buybox
            competitor_display = "we_own_buybox"
        else:
            competitor_price = int(competitor_price) if competitor_price else 500
            optimal_price = engine.calculate_optimal_price(test_price, competitor_price, offer_id)
            competitor_display = competitor_price
        
        return jsonify({
            'offer_id': offer_id,
            'test_price': test_price,
            'competitor_price': competitor_display,
            'competitor_source': source,
            'optimal_price': optimal_price,
            'business_rule': describe_business_rule(test_price, competitor_price, optimal_price),
            'cache_hit': engine._get_cached_price(offer_id) is not None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Detailed health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Takealot Repricing Engine with Proactive Monitoring',
        'version': '2.0.0',
        'feature': 'PROACTIVE MONITORING + Instant Webhook Responses'
    })

@app.route('/debug-webhook', methods=['POST'])
def debug_webhook():
    """Special endpoint to debug webhook structure"""
    webhook_data = request.get_json()
    
    response = {
        'received_fields': list(webhook_data.keys()) if webhook_data else [],
        'full_payload': webhook_data,
        'field_types': {k: type(v).__name__ for k, v in webhook_data.items()} if webhook_data else {}
    }
    
    logger.info(f"🐛 DEBUG WEBHOOK: {response}")
    return jsonify(response)

@app.route('/debug-scraping/<offer_id>')
def debug_scraping(offer_id):
    """Debug endpoint to test real scraping vs mock data"""
    try:
        # Get current mock price
        mock_price = engine._simulate_scraping(offer_id)
        
        # Get real price
        real_price = engine.get_real_competitor_price(offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'mock_price': mock_price,
            'real_price': real_price,
            'price_difference': real_price - mock_price if real_price and real_price != "we_own_buybox" else None,
            'using_real_data': real_price != mock_price if real_price and real_price != "we_own_buybox" else False,
            'real_data_available': real_price is not None and real_price != "we_own_buybox",
            'buybox_owner': "we_own_buybox" if real_price == "we_own_buybox" else "competitor"
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/monitoring/status')
def monitoring_status():
    """Check monitoring system status"""
    return jsonify({
        'monitoring_active': engine.price_monitor.is_monitoring,
        'products_configured': len(engine.product_config),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/monitoring/start')
def start_monitoring():
    """Manually start monitoring"""
    engine.start_background_monitoring()
    return jsonify({'status': 'monitoring_started'})

@app.route('/monitoring/stop')
def stop_monitoring():
    """Manually stop monitoring"""
    engine.price_monitor.stop_monitoring()
    return jsonify({'status': 'monitoring_stopped'})

@app.route('/monitoring/prices')
def get_all_prices():
    """Get all stored competitor prices"""
    try:
        with sqlite3.connect("price_monitor.db") as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM competitor_prices ORDER BY last_updated DESC')
            results = cursor.fetchall()
        
        prices = []
        for row in results:
            prices.append({
                'offer_id': row[0],
                'competitor_price': row[1],
                'last_updated': row[2],
                'source': row[3]
            })
        
        return jsonify({
            'stored_prices': prices,
            'count': len(prices)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/monitoring/update-price', methods=['POST'])
def manual_update_price():
    """Manually update competitor price in monitoring database"""
    try:
        data = request.get_json()
        offer_id = data.get('offer_id')
        price = data.get('competitor_price')
        source = data.get('source', 'manual')
        
        if not offer_id or not price:
            return jsonify({'error': 'Missing offer_id or competitor_price'}), 400
            
        success = engine.price_monitor.store_competitor_price(offer_id, price, source)
        
        return jsonify({
            'status': 'success' if success else 'failed',
            'offer_id': offer_id,
            'competitor_price': price,
            'source': source
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-monitoring-health')
def debug_monitoring_health():
    """Check monitoring system health"""
    monitor = engine.price_monitor
    
    # Check if thread is alive
    thread_alive = monitor.monitoring_thread.is_alive() if monitor.monitoring_thread else False
    
    # Check recent activity
    try:
        with sqlite3.connect("price_monitor.db") as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*), MAX(last_updated) FROM competitor_prices')
            result = cursor.fetchone()
            total_prices, last_updated = result
    except:
        total_prices, last_updated = 0, None
    
    return jsonify({
        'monitoring_active': monitor.is_monitoring,
        'thread_alive': thread_alive,
        'total_products_configured': len(engine.product_config),
        'total_prices_stored': total_prices,
        'last_price_update': last_updated,
        'products_per_minute_estimate': len(engine.product_config) / 30 if monitor.is_monitoring else 0,
        'estimated_completion_time': f"{(len(engine.product_config) * 2) / 3600:.1f} hours" if monitor.is_monitoring else 'N/A'
    })

# ✅ ADD THE DEBUG CSV COLUMNS ENDPOINT RIGHT HERE
@app.route('/debug-csv-columns')
def debug_csv_columns():
    """Check exact CSV column names and structure"""
    try:
        import pandas as pd
        
        # Read CSV without column expectations
        df = pd.read_csv('products_config.csv')
        
        return jsonify({
            'csv_columns': list(df.columns),
            'total_rows': len(df),
            'first_row_data': df.iloc[0].to_dict() if len(df) > 0 else 'empty',
            'column_count': len(df.columns),
            'expected_columns': ['offer_id', 'product_url', 'min_price', 'max_price'],
            'columns_match_expected': set(df.columns) == set(['offer_id', 'product_url', 'min_price', 'max_price'])
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-product-loading-version')
def debug_product_loading_version():
    """Check which product loading version is running"""
    try:
        # Get the source code of the current method
        import inspect
        source = inspect.getsource(engine._load_product_config)
        
        # Check which version is running
        if "OfferID" in source and "SellingPrice" in source:
            version = "OLD VERSION (OfferID, SellingPrice, CostPrice)"
        elif "offer_id" in source and "product_url" in source:
            version = "NEW VERSION (offer_id, product_url, min_price, max_price)"
        else:
            version = "UNKNOWN VERSION"
            
        return jsonify({
            'product_loading_version': version,
            'product_config_loaded': len(engine.product_config),
            'method_source_preview': source[:500]  # First 500 chars
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-product-loading-version')
def debug_product_loading_version():
    # ... existing code

# ✅ ADD THIS RIGHT HERE
@app.route('/debug-csv-first-rows')
def debug_csv_first_rows():
    """Check the first few rows of the CSV directly"""
    try:
        import pandas as pd
        
        df = pd.read_csv('products_config.csv')
        
        # Get first 3 rows as dict
        first_rows = []
        for i in range(min(3, len(df))):
            first_rows.append(df.iloc[i].to_dict())
        
        return jsonify({
            'total_rows': len(df),
            'first_3_rows': first_rows,
            'column_dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()}
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/force-reload-products')
def force_reload_products():
    """Force reload of product configuration"""
    try:
        # Re-initialize the engine to reload products
        global engine
        engine = TakealotRepricingEngine()
        
        return jsonify({
            'status': 'reloaded',
            'products_loaded': len(engine.product_config),
            'sample_products': list(engine.product_config.keys())[:3] if engine.product_config else 'none'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-plid-conversion/<product_id>')
def debug_plid_conversion(product_id):
    """Test PLID extraction for a product - FIXED VERSION"""
    try:
        # Get the product URL from config first
        product_info = engine.product_config.get(product_id, {})
        product_url = product_info.get("product_url")
        
        if not product_url:
            return jsonify({
                "error": f"No product URL found for {product_id}",
                "available_products": list(engine.product_config.keys())[:5] if engine.product_config else "none",
                "product_config_count": len(engine.product_config)
            })
        
        # Extract PLID from the actual URL
        plid = engine.price_monitor._extract_plid_from_url(product_url)
        
        if not plid:
            return jsonify({
                "error": f"Could not extract PLID from URL: {product_url}",
                "product_id": product_id,
                "product_url": product_url
            })
        
        # Test if the PLID works
        test_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.takealot.com/{plid.lower()}",
        }
        
        response = requests.get(test_url, headers=headers, timeout=10)
        return jsonify({
            "product_id": product_id,
            "product_url": product_url,
            "extracted_plid": plid,
            "api_status_code": response.status_code,
            "api_success": response.status_code == 200,
            "response_preview": response.text[:200] if response.status_code != 200 else "Success"
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "product_id": product_id,
            "product_config_available": bool(engine.product_config),
            "product_count": len(engine.product_config) if engine.product_config else 0
        })

@app.route('/debug-product-info/<offer_id>')
def debug_product_info(offer_id):
    """Debug product information and PLID extraction"""
    product_info = engine.product_config.get(offer_id, {})
    
    return jsonify({
        "offer_id": offer_id,
        "product_info": product_info,
        "plid_valid": product_info.get("plid") is not None,
        "has_min_price": "min_price" in product_info,
        "has_max_price": "max_price" in product_info,
        "product_url": product_info.get("product_url", "MISSING"),
        "extracted_plid": product_info.get("plid", "NOT_EXTRACTED")
    })

@app.route('/debug-api-setup')
def debug_api_setup():
    """Debug API credentials and connection"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    api_secret = os.getenv('TAKEALOT_API_SECRET')
    
    # Test API connectivity
    try:
        test_url = "https://api.takealot.com/v1/sellerlistings"
        headers = {
            "X-Api-Key": api_key,
            "X-Api-Secret": api_secret,
        }
        response = requests.get(test_url, headers=headers, timeout=10)
        api_status = response.status_code
    except Exception as e:
        api_status = f"Error: {e}"
    
    return jsonify({
        'api_key_configured': bool(api_key),
        'api_secret_configured': bool(api_secret),
        'api_connection_test': api_status,
        'environment': os.getenv('RAILWAY_ENVIRONMENT', 'unknown')
    })

@app.route('/debug-api-test')
def debug_api_test():
    """Better API connection test"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    api_secret = os.getenv('TAKEALOT_API_SECRET')
    
    debug_info = {
        'api_key_exists': bool(api_key),
        'api_secret_exists': bool(api_secret),
        'api_key_length': len(api_key) if api_key else 0,
        'api_secret_length': len(api_secret) if api_secret else 0,
    }
    
    # Test different API endpoints
    test_endpoints = [
        "https://api.takealot.com/v1/sellerlistings",
        "https://api.takealot.com/v1/offers"
    ]
    
    for endpoint in test_endpoints:
        try:
            headers = {
                "X-Api-Key": api_key,
                "X-Api-Secret": api_secret,
            }
            response = requests.get(endpoint, headers=headers, timeout=10)
            debug_info[endpoint] = {
                'status_code': response.status_code,
                'response_preview': response.text[:100] if response.text else 'empty'
            }
        except Exception as e:
            debug_info[endpoint] = f"Error: {str(e)}"
    
    return jsonify(debug_info)

@app.route('/debug-fix-check')
def debug_fix_check():
    """Check what's wrong with the current setup"""
    return jsonify({
        'environment_variables': {
            'TAKEALOT_API_KEY': bool(os.getenv('TAKEALOT_API_KEY')),
            'TAKEALOT_API_SECRET': bool(os.getenv('TAKEALOT_API_SECRET')),
            'WEBHOOK_SECRET': bool(os.getenv('WEBHOOK_SECRET')),
        },
        'current_working_directory': os.getcwd(),
        'files_in_directory': os.listdir('.'),
        'product_config_loaded': len(engine.product_config),
        'sample_product': list(engine.product_config.keys())[:3] if engine.product_config else 'none'
    })

@app.route('/debug-raw-api/<offer_id>')
def debug_raw_api(offer_id):
    """Get raw API response to see actual structure"""
    try:
        # Get PLID from product config
        product_info = engine.product_config.get(offer_id, {})
        plid = product_info.get("plid", f"PLID{offer_id}")
        
        api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        
        response = requests.get(api_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'status': 'success',
                'offer_id': offer_id,
                'plid_used': plid,
                'api_structure': data,
                'product_keys': list(data.get('product', {}).keys()) if data.get('product') else []
            })
        else:
            return jsonify({
                'status': 'error',
                'status_code': response.status_code,
                'response_text': response.text
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-product-status/<offer_id>')
def debug_product_status(offer_id):
    """Check current product status and pricing"""
    try:
        # Get current competitor price
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        
        # Get your product config
        min_price, max_price = engine.get_product_thresholds(offer_id)
        
        # Calculate what price should be set
        optimal_price = engine.calculate_optimal_price(743, competitor_price, offer_id)  # Using 743 as current
        
        return jsonify({
            'offer_id': offer_id,
            'competitor_price': competitor_price,
            'competitor_source': source,
            'your_min_price': min_price,
            'your_max_price': max_price,
            'calculated_optimal_price': optimal_price,
            'business_logic_applied': describe_business_rule(743, competitor_price, optimal_price),
            'expected_action': 'REVERT_TO_MAX' if competitor_price < min_price else 'R1_BELOW_COMPETITOR'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-env-all')
def debug_env_all():
    """Debug all environment variables (redacted for security)"""
    all_vars = dict(os.environ)
    
    # Redact sensitive values but show if they exist
    debug_info = {}
    for key, value in all_vars.items():
        if 'API' in key or 'KEY' in key or 'SECRET' in key:
            debug_info[key] = {
                'exists': True,
                'length': len(value),
                'value_preview': value[:4] + '...' if value else 'empty'
            }
        else:
            debug_info[key] = {
                'exists': True,
                'value': value
            }
    
    return jsonify(debug_info)

@app.route('/debug-env-detailed')
def debug_env_detailed():
    """Detailed environment variable debugging"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    api_secret = os.getenv('TAKEALOT_API_SECRET')
    
    return jsonify({
        'api_key_raw': api_key if api_key else 'NOT_FOUND',
        'api_secret_raw': api_secret if api_secret else 'NOT_FOUND',
        'api_key_length': len(api_key) if api_key else 0,
        'api_secret_length': len(api_secret) if api_secret else 0,
        'api_key_preview': f"{api_key[:10]}..." if api_key and len(api_key) > 10 else api_key,
        'api_secret_preview': f"{api_secret[:10]}..." if api_secret and len(api_secret) > 10 else api_secret,
        'all_env_vars': {k: 'REDACTED' for k in os.environ.keys() if 'API' in k or 'KEY' in k or 'SECRET' in k}
    })

@app.route('/debug-railway-vars')
def debug_railway_vars():
    """Debug Railway-specific environment variables"""
    all_vars = dict(os.environ)
    
    # Look for Railway and API related variables
    railway_vars = {}
    for key, value in all_vars.items():
        if 'RAILWAY' in key or 'API' in key or 'KEY' in key or 'SECRET' in key:
            railway_vars[key] = {
                'exists': True,
                'length': len(value),
                'value_preview': value[:8] + '...' if len(value) > 8 else value
            }
    
    return jsonify({
        'railway_environment_vars': railway_vars,
        'total_vars_found': len(railway_vars),
        'note': 'If TAKEALOT_API_KEY is missing, trigger a redeploy in Railway'
    })

@app.route('/debug-api-simple')
def debug_api_simple():
    """Simple API endpoint test using CORRECT format"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    
    # Use the CORRECT endpoint from your working app
    BASE_URL = "https://seller-api.takealot.com"
    endpoint = f"{BASE_URL}/v2/offers"
    
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        return jsonify({
            'endpoint': endpoint,
            'status_code': response.status_code,
            'response_text': response.text[:500] if response.text else 'empty',
            'credentials_available': bool(api_key),
            'using_correct_format': True
        })
    except Exception as e:
        return jsonify({
            'endpoint': endpoint,
            'error': str(e),
            'credentials_available': bool(api_key),
            'using_correct_format': True
        })

@app.route('/debug-plid-test/<offer_id>')
def debug_plid_test(offer_id):
    """Test PLID identifier specifically"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    BASE_URL = "https://seller-api.takealot.com"
    
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json"
    }
    
    # Test getting offer by PLID
    endpoint = f"{BASE_URL}/v2/offers/offer?identifier=PLID{offer_id}"
    
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        return jsonify({
            'endpoint': endpoint,
            'status_code': response.status_code,
            'response_text': response.text,
            'plid_used': f"PLID{offer_id}"
        })
    except Exception as e:
        return jsonify({
            'endpoint': endpoint,
            'error': str(e),
            'plid_used': f"PLID{offer_id}"
        })

@app.route('/debug-find-offer/<search_term>')
def debug_find_offer(search_term):
    """Search for offers to find the correct identifier"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    BASE_URL = "https://seller-api.takealot.com"
    
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json"
    }
    
    # Get all offers and search for our product
    endpoint = f"{BASE_URL}/v2/offers"
    
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        if response.status_code == 200:
            offers_data = response.json()
            all_offers = offers_data.get('offers', [])
            
            # Search for offers that might be our product
            matching_offers = []
            for offer in all_offers:
                # Check various fields that might contain our product info
                offer_info = {
                    'offer_id': offer.get('offer_id'),
                    'tsin_id': offer.get('tsin_id'),
                    'sku': offer.get('sku'),
                    'barcode': offer.get('barcode'),
                    'title': offer.get('title'),
                    'selling_price': offer.get('selling_price'),
                    'status': offer.get('status')
                }
                
                # Add if it matches our search term or looks relevant
                matching_offers.append(offer_info)
            
            return jsonify({
                'search_term': search_term,
                'total_offers': len(all_offers),
                'matching_offers': matching_offers[:10],  # First 10 offers
                'note': 'Look for offer_id, sku, or barcode to use as identifier'
            })
        else:
            return jsonify({
                'error': f'API returned {response.status_code}',
                'response_text': response.text
            })
            
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/debug-search-sanding-disc')
def debug_search_sanding_disc():
    """Search for the sanding disc product in your offers"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    BASE_URL = "https://seller-api.takealot.com"
    
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json"
    }
    
    # Get all offers
    endpoint = f"{BASE_URL}/v2/offers"
    
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        if response.status_code == 200:
            offers_data = response.json()
            all_offers = offers_data.get('offers', [])
            
            # Search for sanding disc related products
            sanding_offers = []
            for offer in all_offers:
                title = offer.get('title', '').lower()
                if any(keyword in title for keyword in ['sanding', 'disc', 'abrasive', 'psa']):
                    sanding_offers.append({
                        'offer_id': offer.get('offer_id'),
                        'title': offer.get('title'),
                        'selling_price': offer.get('selling_price'),
                        'status': offer.get('status'),
                        'sku': offer.get('sku'),
                        'barcode': offer.get('barcode')
                    })
            
            return jsonify({
                'sanding_disc_offers': sanding_offers,
                'total_offers_searched': len(all_offers),
                'note': 'Look for the sanding disc product in your seller account'
            })
        else:
            return jsonify({'error': f'API returned {response.status_code}'})
            
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/debug-test-barcode-update/<barcode>/<int:new_price>')
def debug_test_barcode_update(barcode, new_price):
    """Test price update with barcode specifically"""
    api_key = os.getenv('TAKEALOT_API_KEY')
    BASE_URL = "https://seller-api.takealot.com"
    
    endpoint = f"{BASE_URL}/v2/offers/offer?identifier=BARCODE{barcode}"
    
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "selling_price": int(new_price)
    }
    
    try:
        response = requests.patch(endpoint, json=payload, headers=headers, timeout=30)
        return jsonify({
            'barcode': barcode,
            'endpoint': endpoint,
            'status_code': response.status_code,
            'response_text': response.text,
            'new_price': new_price
        })
    except Exception as e:
        return jsonify({
            'barcode': barcode,
            'error': str(e),
            'endpoint': endpoint
        })

@app.route('/test-update/<offer_id>/<int:new_price>')
def test_price_update(offer_id, new_price):
    """Test endpoint for price updates - minimal restrictions for testing"""
    try:
        # Basic safety - just ensure it's a reasonable price (> R1, < R5000)
        if new_price < 1 or new_price > 5000:
            return jsonify({'error': 'Price must be between R1 and R5000 for testing'}), 400
        
        # Get actual thresholds for info (but don't restrict)
        min_price, max_price = engine.get_product_thresholds(offer_id)
        
        success = engine.update_price_with_retry(offer_id, new_price)
        
        return jsonify({
            'offer_id': offer_id,
            'new_price': new_price,
            'update_success': success,
            'test_note': 'THIS IS A REAL API CALL - price will actually change on Takealot!',
            'your_min_price': min_price,
            'your_max_price': max_price,
            'warning': 'This is a REAL price update on Takealot - use carefully!'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def describe_business_rule(my_price, competitor_price, optimal_price):
    """Describe which business rule was applied"""
    # Get thresholds for the specific product (simplified for this function)
    MIN_PRICE = 500  # Default fallback (WHOLE NUMBER)
    MAX_PRICE = 700  # Default fallback (WHOLE NUMBER)
    
    my_price = int(my_price)
    optimal_price = int(optimal_price)
    
    if competitor_price == "we_own_buybox":
        return "WE_OWN_BUYBOX - No adjustment needed"
    
    competitor_price = int(competitor_price) if competitor_price and competitor_price != "we_own_buybox" else 0
    
    if competitor_price < MIN_PRICE:
        return f"REVERT_TO_MAX - Competitor R{competitor_price} < Min R{MIN_PRICE}"
    else:
        return f"R1_BELOW_COMPETITOR - Optimal price R{optimal_price} = Competitor R{competitor_price} - R1"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Takealot Repricing Engine on port {port}")
    logger.info(f"🎯 FEATURE: PROACTIVE MONITORING + Instant Webhook Responses")
    
    # Start background monitoring when running directly
    def start_monitoring_delayed():
        time.sleep(10)
        engine.start_background_monitoring()
    
    monitoring_thread = threading.Thread(target=start_monitoring_delayed, daemon=True)
    monitoring_thread.start()
    
    app.run(host='0.0.0.0', port=port, debug=False)