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

# Configure logging FIRST - before anything else
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Log startup
logger.info("=" * 50)
logger.info("🚀 APPLICATION STARTING")
logger.info("=" * 50)

app = Flask(__name__)

# Constants
PRICE_FRESHNESS_SECONDS = 3600
MONITORING_INTERVAL_MINUTES = 30
MIN_REQUEST_INTERVAL = 3.0

class PriceMonitor:
    def __init__(self, engine_ref=None):
        self.db_file = "price_monitor.db"
        self.engine_ref = engine_ref
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
                
                # New table for tracking YOUR price changes
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS my_price_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        offer_id TEXT NOT NULL,
                        old_price REAL,
                        new_price REAL,
                        competitor_price REAL,
                        reason TEXT,
                        success BOOLEAN,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        """Get stored competitor price"""
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
                try:
                    last_time = datetime.fromisoformat(last_updated)
                    time_diff = (datetime.now() - last_time).total_seconds()
                    
                    if time_diff < PRICE_FRESHNESS_SECONDS:
                        logger.info(f"💾 Using FRESH stored price for {offer_id}: R{price}")
                        return price
                except Exception as e:
                    logger.error(f"❌ Error parsing timestamp: {e}")
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
        logger.info(f"📅 Will scrape every {interval_minutes} minutes")
        logger.info(f"⏱️ Estimated time per cycle: {(len(product_list) * 2) / 60:.1f} minutes")
    
    def _monitoring_loop(self, product_list, interval_minutes):
        """Background loop to monitor all products"""
        while self.is_monitoring:
            try:
                logger.info("="*60)
                logger.info(f"🔄 MONITORING CYCLE STARTED for {len(product_list)} products")
                logger.info("="*60)
                
                success_count = 0
                error_count = 0
                start_time = time.time()
                
                for idx, offer_id in enumerate(product_list):
                    if not self.is_monitoring:
                        break
                    
                    try:
                        # Progress indicator
                        if (idx + 1) % 100 == 0:
                            logger.info(f"📊 Progress: {idx + 1}/{len(product_list)} products")
                        
                        # Scrape competitor price
                        competitor_price = self._direct_scrape_price(offer_id)
                        
                        if competitor_price and competitor_price > 0 and competitor_price != "we_own_buybox":
                            self.store_competitor_price(offer_id, competitor_price, "background_monitor")
                            success_count += 1
                        elif competitor_price == "we_own_buybox":
                            # Store special marker for buybox ownership
                            self.store_competitor_price(offer_id, -1, "we_own_buybox")
                            success_count += 1
                        else:
                            error_count += 1
                        
                        # Rate limiting: 2 seconds between requests
                        time.sleep(2)
                        
                    except Exception as e:
                        logger.error(f"❌ Monitoring failed for {offer_id}: {e}")
                        error_count += 1
                        time.sleep(5)
                
                elapsed_time = time.time() - start_time
                
                logger.info("="*60)
                logger.info(f"✅ MONITORING CYCLE COMPLETED")
                logger.info(f"   Success: {success_count}/{len(product_list)}")
                logger.info(f"   Errors: {error_count}")
                logger.info(f"   Time taken: {elapsed_time/60:.1f} minutes")
                logger.info(f"⏰ Next cycle in {interval_minutes} minutes")
                logger.info("="*60)
                
                if self.is_monitoring:
                    time.sleep(interval_minutes * 60)
                    
            except Exception as e:
                logger.error(f"❌ Monitoring loop error: {e}")
                import traceback
                logger.error(f"Stack trace: {traceback.format_exc()}")
                if self.is_monitoring:
                    time.sleep(60)

    def _direct_scrape_price(self, offer_id):
        """Direct price scraping for monitoring - uses the same logic as real scraping"""
        try:
            if not self.engine_ref:
                logger.warning(f"⚠️ No engine reference for {offer_id}")
                return None
                
            product_info = self.engine_ref.product_config.get(offer_id, {})
            plid = product_info.get("plid")
            
            if not plid:
                logger.warning(f"⚠️ No PLID for {offer_id}")
                return None
            
            # Call Takealot API
            api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://www.takealot.com/{plid.lower()}",
            }
            
            response = requests.get(api_url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                logger.debug(f"⚠️ API {response.status_code} for {offer_id}")
                return None
            
            data = response.json()
            
            # Extract prices - Takealot returns prices in RANDS
            price_candidates = []
            
            # Buybox items (most reliable)
            buybox = data.get("buybox", {})
            if buybox:
                items = buybox.get("items", [])
                for item in items:
                    price = item.get("price")
                    if price and price > 0:
                        price_rand = float(price)
                        price_candidates.append(price_rand)
            
            if price_candidates:
                lowest_price = min(price_candidates)
                return lowest_price
            
            return None
                
        except Exception as e:
            logger.debug(f"⚠️ Scrape failed for {offer_id}: {e}")
            return None

    def stop_monitoring(self):
        """Stop background monitoring"""
        self.is_monitoring = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=10)
        logger.info("🛑 Background monitoring stopped")

    def log_price_change(self, offer_id, old_price, new_price, competitor_price, reason, success):
        """Log YOUR price changes to database"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO my_price_history 
                    (offer_id, old_price, new_price, competitor_price, reason, success, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (str(offer_id), old_price, new_price, competitor_price, reason, success, datetime.now().isoformat()))
                conn.commit()
            logger.info(f"📝 Logged price change: {offer_id} R{old_price}→R{new_price}")
        except Exception as e:
            logger.error(f"❌ Failed to log price change: {e}")

    def _direct_scrape_price(self, offer_id):
        """Direct price scraping for monitoring - uses the same logic as real scraping"""
        try:
            if not self.engine_ref:
                logger.warning(f"⚠️ No engine reference for {offer_id}")
                return None
                
            product_info = self.engine_ref.product_config.get(offer_id, {})
            plid = product_info.get("plid")
            
            if not plid:
                logger.warning(f"⚠️ No PLID for {offer_id}")
                return None
            
            # Call Takealot API
            api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://www.takealot.com/{plid.lower()}",
            }
            
            response = requests.get(api_url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                logger.debug(f"⚠️ API {response.status_code} for {offer_id}")
                return None
            
            data = response.json()
            
            # Extract prices - Takealot returns prices in RANDS
            price_candidates = []
            
            # Buybox items (most reliable)
            buybox = data.get("buybox", {})
            if buybox:
                items = buybox.get("items", [])
                for item in items:
                    price = item.get("price")
                    if price and price > 0:
                        price_rand = float(price)
                        price_candidates.append(price_rand)
            
            if price_candidates:
                lowest_price = min(price_candidates)
                return lowest_price
            
            return None
                
        except Exception as e:
            logger.debug(f"⚠️ Scrape failed for {offer_id}: {e}")
            return None

class TakealotRepricingEngine:
    def __init__(self):
        logger.info("🔧 Initializing TakealotRepricingEngine...")
        
        self.session = requests.Session()
        self.price_cache = {}
        self.cache_ttl = 3600
        self.last_request_time = 0
        self.min_request_interval = MIN_REQUEST_INTERVAL

        # Load product configurations - CRASH-PROOF
        self.product_config = self._load_product_config_safe()
        
        # Initialize price monitor only if we have products
        if self.product_config:
            self.price_monitor = PriceMonitor(engine_ref=self)
            logger.info(f"✅ Engine initialized with {len(self.product_config)} products")
        else:
            self.price_monitor = PriceMonitor(engine_ref=None)
            logger.warning("⚠️ Engine initialized with NO products")

    def _load_product_config_safe(self):
        """CRASH-PROOF product config loading"""
        try:
            logger.info("📂 Starting SAFE product config loading...")
            
            file_path = 'products_config.csv'
            
            # Check if file exists
            if not os.path.exists(file_path):
                logger.warning(f"⚠️ {file_path} NOT FOUND - continuing with empty config")
                return {}

            # Try to read CSV - FORCE offer_id as STRING to prevent scientific notation
            try:
                df = pd.read_csv(file_path, dtype={'offer_id': str})
                logger.info(f"📊 CSV loaded: {len(df)} rows, {len(df.columns)} columns")
                logger.info(f"📋 Columns found: {list(df.columns)}")
                
                # Log first few offer_ids to verify format
                if len(df) > 0:
                    sample_ids = df['offer_id'].head(3).tolist() if 'offer_id' in df.columns else []
                    logger.info(f"📋 Sample offer_ids: {sample_ids}")
            except Exception as e:
                logger.error(f"❌ Failed to read CSV: {e}")
                return {}
            
            # Handle EMPTY CSV
            if len(df) == 0:
                logger.warning("⚠️ CSV is empty - continuing with empty config")
                return {}
            
            # Detect column format and map to standard format
            column_mapping = self._detect_csv_format(df.columns)
            
            if not column_mapping:
                logger.error("❌ Could not detect CSV format")
                logger.error(f"Available columns: {list(df.columns)}")
                return {}
            
            logger.info(f"✅ Detected CSV format: {column_mapping}")
            
            # Build config dictionary
            config_dict = {}
            success_count = 0
            error_count = 0
            
            for idx, row in df.iterrows():
                try:
                    # Extract values using column mapping
                    offer_id = str(row[column_mapping['offer_id']]).strip()
                    product_url = str(row[column_mapping['product_url']]).strip()
                    min_price = float(row[column_mapping['min_price']])
                    max_price = float(row[column_mapping['max_price']])
                    
                    # Skip invalid rows
                    if pd.isna(offer_id) or offer_id == 'nan' or not product_url or product_url == 'nan':
                        logger.debug(f"⏭️ Skipping invalid row {idx}")
                        error_count += 1
                        continue
                    
                    # Extract PLID from URL
                    plid = self._extract_plid_from_url(product_url)
                    
                    config_dict[offer_id] = {
                        'min_price': min_price,
                        'max_price': max_price,
                        'product_url': product_url,
                        'plid': plid
                    }
                    success_count += 1
                    
                except Exception as e:
                    logger.debug(f"⚠️ Error processing row {idx}: {e}")
                    error_count += 1
                    continue
            
            logger.info(f"✅ Successfully loaded {success_count} products")
            if error_count > 0:
                logger.info(f"⚠️ Skipped {error_count} invalid rows")
            
            return config_dict
            
        except Exception as e:
            logger.error(f"❌ CRITICAL: Product config loading failed: {e}")
            logger.error(f"📍 Error details: {type(e).__name__}")
            import traceback
            logger.error(f"📍 Traceback: {traceback.format_exc()}")
            return {}

    def _detect_csv_format(self, columns):
        """Detect which CSV format is being used"""
        columns_lower = [col.lower().strip() for col in columns]
        
        # Format 1: offer_id, product_url, min_price, max_price
        format1 = {
            'offer_id': None,
            'product_url': None,
            'min_price': None,
            'max_price': None
        }
        
        for col in columns:
            col_lower = col.lower().strip()
            
            # Match offer_id variants
            if col_lower in ['offer_id', 'offerid', 'offer id', 'id']:
                format1['offer_id'] = col
            
            # Match product_url variants
            elif col_lower in ['product_url', 'producturl', 'product url', 'url', 'link']:
                format1['product_url'] = col
            
            # Match min_price variants
            elif col_lower in ['min_price', 'minprice', 'min price', 'minimum_price', 'cost_price', 'costprice']:
                format1['min_price'] = col
            
            # Match max_price variants
            elif col_lower in ['max_price', 'maxprice', 'max price', 'maximum_price', 'selling_price', 'sellingprice']:
                format1['max_price'] = col
        
        # Check if all required fields were found
        if all(format1.values()):
            return format1
        
        logger.warning(f"⚠️ Could not map all columns. Found: {format1}")
        return None

    def _extract_plid_from_url(self, product_url):
        """Extract PLID from URL"""
        try:
            plid_match = re.search(r'PLID(\d+)', product_url, re.IGNORECASE)
            if plid_match:
                return f"PLID{plid_match.group(1)}"
            return None
        except:
            return None

    def get_product_thresholds(self, offer_id):
        """Get min_price and max_price for specific product - NO FALLBACK"""
        offer_id_str = str(offer_id)

        if offer_id_str in self.product_config:
            config = self.product_config[offer_id_str]
            return config.get('min_price'), config.get('max_price')
        else:
            # 🚨 NO FALLBACK - This should never be called for unknown products
            logger.error(f"🚨 CRITICAL: get_product_thresholds called for unknown offer_id '{offer_id_str}'")
            raise ValueError(f"Product {offer_id_str} not in configuration")

    def get_competitor_price_instant(self, offer_id):
        """INSTANT competitor price lookup"""
        stored_price = self.price_monitor.get_competitor_price(offer_id)
        if stored_price is not None:
            return stored_price, 'proactive_monitoring'
        
        logger.info("🔄 No stored price, using real-time scraping")
        real_time_price = self.get_competitor_price(offer_id)
        return real_time_price, 'real_time_scraping'

    def get_competitor_price(self, offer_id):
        """Get competitor price with fallback - NO SIMULATION FOR UNKNOWN PRODUCTS"""
        try:
            # 🚨 SAFETY: Only process products in config
            if str(offer_id) not in self.product_config:
                logger.error(f"🚨 REJECTED: {offer_id} not in product config")
                return None
            
            # 1. Check stored price first
            stored_price = self.price_monitor.get_competitor_price(offer_id)
            if stored_price is not None:
                logger.info(f"💾 Using stored price for {offer_id}: R{stored_price}")
                return stored_price
            
            # 2. Check cache
            cached_price = self._get_cached_price(offer_id)
            if cached_price is not None:
                logger.info(f"💾 Using cached price for {offer_id}: R{cached_price}")
                return cached_price
            
            # 3. Try REAL scraping ONLY
            logger.info(f"🌐 Attempting REAL scraping for {offer_id}")
            real_price = self._scrape_real_competitor_price(offer_id)
            
            if real_price and real_price > 0:
                self._cache_price(offer_id, real_price)
                # Also store in database for future use
                self.price_monitor.store_competitor_price(offer_id, real_price, "real_time_scraping")
                return real_price
            elif real_price == "we_own_buybox":
                return "we_own_buybox"
            
            # 4. NO SIMULATION - Return None if can't get real price
            logger.error(f"🚨 Cannot get real competitor price for {offer_id}")
            return None
            
        except Exception as e:
            logger.error(f"❌ All price methods failed: {e}")
            return None

    def _scrape_real_competitor_price(self, offer_id):
        """Scrape actual competitor price from Takealot API"""
        try:
            # Get product info
            product_info = self.product_config.get(str(offer_id), {})
            plid = product_info.get('plid')
            
            if not plid:
                logger.warning(f"⚠️ No PLID for {offer_id}")
                return None
            
            # Rate limiting
            self._respect_rate_limit()
            
            # Call Takealot API
            api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://www.takealot.com/{plid.lower()}",
            }
            
            logger.info(f"🌐 Fetching: {api_url}")
            response = self.session.get(api_url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"❌ API returned {response.status_code}")
                return None
            
            data = response.json()
            
            # Extract prices - Takealot returns prices in RANDS (not cents!)
            price_candidates = []
            
            # Method 1: Buybox items (most reliable)
            buybox = data.get("buybox", {})
            if buybox:
                items = buybox.get("items", [])
                for item in items:
                    price = item.get("price")
                    if price and price > 0:
                        price_rand = float(price)  # Already in Rands!
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Buybox item price: R{price_rand}")
            
            # Method 2: Check if we own the buybox (would need seller info)
            # Note: The API doesn't clearly show seller_id in the buybox for this product
            # We'll need to handle this in the webhook when we see our own offer_id
            
            if price_candidates:
                lowest_price = min(price_candidates)
                logger.info(f"🎯 Selected competitor price: R{lowest_price}")
                return lowest_price
            
            logger.warning(f"⚠️ No prices found in API response")
            return None
            
        except Exception as e:
            logger.error(f"❌ Real scraping failed: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            return None

    def _respect_rate_limit(self):
        """Respect rate limiting between API calls"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last + random.uniform(0.5, 1.5)
            logger.info(f"⏳ Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()

    def calculate_optimal_price(self, my_price, competitor_price, offer_id):
        """Calculate optimal price using business logic"""
        min_price, max_price = self.get_product_thresholds(offer_id)
        
        my_price = int(my_price)
        min_price = int(min_price)
        max_price = int(max_price)
        
        logger.info(f"🧮 Calculating price for {offer_id}")
        logger.info(f"   My price: R{my_price}, Min: R{min_price}, Max: R{max_price}")
        
        if competitor_price == "we_own_buybox":
            logger.info("🎉 WE OWN THE BUYBOX - no adjustment")
            return my_price
        
        competitor_price = int(competitor_price) if competitor_price and competitor_price != "we_own_buybox" else None
        
        if not competitor_price or competitor_price <= 0:
            logger.warning("⚠️ No valid competitor price - using max price")
            return max_price
        
        logger.info(f"   Competitor price: R{competitor_price}")
        
        if competitor_price < min_price:
            logger.info(f"   🔄 REVERT: Competitor R{competitor_price} < min R{min_price} → R{max_price}")
            return max_price
        
        new_price = competitor_price - 1
        
        if new_price < min_price:
            logger.info(f"   ⚠️ ADJUSTMENT: R{new_price} < min R{min_price} → R{max_price}")
            return max_price
        
        if new_price > max_price:
            logger.info(f"   ⚠️ ADJUSTMENT: R{new_price} > max R{max_price} → R{max_price}")
            return max_price
        
        logger.info(f"   📉 ADJUST: R1 below competitor R{competitor_price} → R{new_price}")
        return new_price

    def update_price(self, offer_id, new_price):
        """Update price on Takealot"""
        try:
            api_key = os.getenv('TAKEALOT_API_KEY')
            if not api_key:
                logger.error("❌ TAKEALOT_API_KEY not set")
                return False
            
            BASE_URL = "https://seller-api.takealot.com"
            endpoint = f"{BASE_URL}/v2/offers/offer?identifier={offer_id}"
            
            headers = {
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {"selling_price": int(new_price)}
            
            response = self.session.patch(endpoint, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"✅ Updated {offer_id} to R{new_price}")
                return True
            else:
                logger.error(f"❌ Update failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Price update failed: {e}")
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

    def _simulate_scraping(self, offer_id):
        """Fallback: Generate random prices"""
        time.sleep(0.5)
        offer_id_str = str(offer_id)
        hash_obj = hashlib.md5(offer_id_str.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        base_price = 450 + (hash_int % 200)
        logger.info(f"🔄 Using simulated price: R{base_price}")
        return float(base_price)

    def _get_fallback_price(self, offer_id):
        offer_id_str = str(offer_id)
        hash_obj = hashlib.md5(offer_id_str.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        fallback_price = 500 + (hash_int % 100)
        logger.warning(f"🔄 Using fallback price: R{fallback_price}")
        return float(fallback_price)

    def start_background_monitoring(self):
        """Start monitoring all configured products"""
        if not self.product_config:
            logger.warning("⚠️ No products configured for monitoring")
            return
        
        product_list = list(self.product_config.keys())
        logger.info(f"🚀 Starting background monitoring for {len(product_list)} products")
        self.price_monitor.start_monitoring(product_list, interval_minutes=30)

    def stop_monitoring(self):
        """Stop background monitoring"""
        if self.price_monitor:
            self.price_monitor.stop_monitoring()

# Initialize the engine - WRAPPED IN TRY-CATCH
try:
    logger.info("🔧 Creating engine instance...")
    engine = TakealotRepricingEngine()
    logger.info("✅ Engine created successfully")
    
except Exception as e:
    logger.error(f"❌ FATAL: Engine creation failed: {e}")
    import traceback
    logger.error(f"📍 Traceback: {traceback.format_exc()}")
    # Create a dummy engine so app can at least start
    engine = None

# Start monitoring in a separate thread after a delay
def delayed_monitoring_start():
    """Start monitoring after a delay to ensure engine is ready"""
    time.sleep(10)  # Wait 10 seconds for app to fully start
    if engine and engine.product_config:
        logger.info("🚀 Starting background monitoring...")
        try:
            engine.start_background_monitoring()
            logger.info("✅ Background monitoring started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start monitoring: {e}")
    else:
        logger.error("❌ Cannot start monitoring: Engine not initialized or no products loaded")

# Start monitoring thread
monitoring_startup_thread = threading.Thread(target=delayed_monitoring_start, daemon=True)
monitoring_startup_thread.start()

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Takealot Repricing Engine',
        'version': '2.1.0',
        'timestamp': datetime.now().isoformat(),
        'engine_loaded': engine is not None,
        'products_loaded': len(engine.product_config) if engine else 0
    })

@app.route('/health')
def health():
    """Detailed health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Takealot Repricing Engine',
        'version': '2.1.0',
        'engine_status': 'loaded' if engine else 'failed',
        'products_count': len(engine.product_config) if engine else 0
    })

@app.route('/debug-startup')
def debug_startup():
    """Debug startup information"""
    return jsonify({
        'engine_exists': engine is not None,
        'products_loaded': len(engine.product_config) if engine else 0,
        'csv_exists': os.path.exists('products_config.csv'),
        'working_directory': os.getcwd(),
        'files_in_directory': os.listdir('.'),
        'sample_products': list(engine.product_config.keys())[:5] if engine and engine.product_config else []
    })

@app.route('/debug-csv-info')
def debug_csv_info():
    """Show CSV information"""
    try:
        if not os.path.exists('products_config.csv'):
            return jsonify({'error': 'CSV file not found'})
        
        df = pd.read_csv('products_config.csv')
        
        return jsonify({
            'columns': list(df.columns),
            'row_count': len(df),
            'first_row': df.iloc[0].to_dict() if len(df) > 0 else 'empty',
            'column_types': {col: str(dtype) for col, dtype in df.dtypes.items()}
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/webhook/price-change', methods=['POST'])
def handle_price_change():
    """Main webhook endpoint"""
    try:
        if not engine:
            return jsonify({'error': 'Engine not initialized'}), 500
        
        webhook_data = request.get_json()
        logger.info(f"📥 Webhook received: {webhook_data}")
        
        offer_id = webhook_data.get('offer_id')
        if not offer_id:
            return jsonify({'error': 'Missing offer_id'}), 400
        
        # 🚨 SAFETY CHECK 1: Reject unknown products (but return 200 OK to Takealot)
        if str(offer_id) not in engine.product_config:
            logger.warning(f"⏭️ SKIPPED: offer_id '{offer_id}' not in products_config.csv")
            return jsonify({
                'status': 'skipped',
                'reason': 'offer_id not in configuration',
                'offer_id': offer_id,
                'message': 'Product not configured for repricing - no action taken'
            }), 200  # ✅ Return 200 OK instead of 400
        
        # Extract current price
        values_changed = webhook_data.get('values_changed', '{}')
        my_current_price = None  # Changed from 500 to None
        
        try:
            if isinstance(values_changed, str):
                values_dict = json.loads(values_changed)
            else:
                values_dict = values_changed
            
            my_current_price = values_dict.get('selling_price', {}).get('new_value')
        except Exception as e:
            logger.error(f"❌ Failed to parse webhook data: {e}")
        
        # 🚨 SAFETY CHECK: Skip if we can't determine current price
        if my_current_price is None:
            logger.warning(f"⏭️ SKIPPED: Could not extract current price from webhook for {offer_id}")
            return jsonify({
                'status': 'skipped',
                'reason': 'invalid_webhook_data',
                'offer_id': offer_id,
                'message': 'Unable to parse current price from webhook - no action taken'
            }), 200
        
        # Get competitor price
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        
        # 🚨 SAFETY CHECK 2: Don't use simulated prices
        if source == 'real_time_scraping' and (not competitor_price or competitor_price == "we_own_buybox"):
            # Try one more time to get real price
            logger.warning(f"⚠️ No valid competitor price for {offer_id}, attempting real scrape")
            competitor_price = engine._scrape_real_competitor_price(offer_id)
            
            if not competitor_price or competitor_price <= 0:
                logger.warning(f"⏭️ SKIPPED: Cannot get real competitor price for {offer_id}")
                return jsonify({
                    'status': 'skipped',
                    'reason': 'cannot_get_real_competitor_price',
                    'offer_id': offer_id,
                    'message': 'Unable to fetch competitor price - no action taken'
                }), 200  # ✅ Return 200 OK instead of 400
        
        # Calculate optimal price
        optimal_price = engine.calculate_optimal_price(my_current_price, competitor_price, offer_id)
        
        # Update if needed
        needs_update = optimal_price != my_current_price
        update_success = False
        
        if needs_update:
            update_success = engine.update_price(offer_id, optimal_price)
            
            # Log the price change
            reason = "competitor_below_min" if competitor_price != "we_own_buybox" and competitor_price < engine.get_product_thresholds(offer_id)[0] else "undercut_competitor"
            engine.price_monitor.log_price_change(
                offer_id=offer_id,
                old_price=my_current_price,
                new_price=optimal_price,
                competitor_price=competitor_price if competitor_price != "we_own_buybox" else 0,
                reason=reason,
                success=update_success
            )
        
        return jsonify({
            'status': 'updated' if update_success else 'no_change',
            'offer_id': offer_id,
            'your_current_price': int(my_current_price),
            'competitor_price': int(competitor_price) if competitor_price != "we_own_buybox" else "we_own_buybox",
            'calculated_price': optimal_price,
            'price_updated': update_success,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/test/<offer_id>')
def test_endpoint(offer_id):
    """Test endpoint"""
    try:
        if not engine:
            return jsonify({'error': 'Engine not initialized'}), 500
        
        test_price = 500
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        optimal_price = engine.calculate_optimal_price(test_price, competitor_price, offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'test_price': test_price,
            'competitor_price': competitor_price,
            'competitor_source': source,
            'optimal_price': optimal_price
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug-product-info/<offer_id>')
def debug_product_info(offer_id):
    """Debug product information"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    product_info = engine.product_config.get(offer_id, {})
    
    return jsonify({
        "offer_id": offer_id,
        "found_in_config": offer_id in engine.product_config,
        "product_info": product_info,
        "total_products_loaded": len(engine.product_config),
        "sample_offer_ids": list(engine.product_config.keys())[:10]
    })

@app.route('/search-product/<search_term>')
def search_product(search_term):
    """Search for products containing term"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    matching = []
    for offer_id, config in engine.product_config.items():
        if search_term in offer_id or search_term in config.get('product_url', ''):
            matching.append({
                'offer_id': offer_id,
                'product_url': config.get('product_url'),
                'min_price': config.get('min_price'),
                'max_price': config.get('max_price'),
                'plid': config.get('plid')
            })
            if len(matching) >= 20:  # Limit results
                break
    
    return jsonify({
        'search_term': search_term,
        'matches_found': len(matching),
        'results': matching
    })

@app.route('/list-products')
def list_products():
    """List first 50 products"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    products = []
    for i, (offer_id, config) in enumerate(engine.product_config.items()):
        if i >= 50:
            break
        products.append({
            'offer_id': offer_id,
            'product_url': config.get('product_url'),
            'min_price': config.get('min_price'),
            'max_price': config.get('max_price'),
            'plid': config.get('plid')
        })
    
    return jsonify({
        'total_products': len(engine.product_config),
        'showing': len(products),
        'products': products
    })

@app.route('/debug-real-scrape/<offer_id>')
def debug_real_scrape(offer_id):
    """Test real scraping for a product with detailed debugging"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        # Get product info
        product_info = engine.product_config.get(offer_id, {})
        plid = product_info.get('plid')
        
        if not plid:
            return jsonify({
                'error': 'No PLID found',
                'offer_id': offer_id,
                'product_info': product_info
            })
        
        # Try to fetch from API
        api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.takealot.com/{plid.lower()}",
        }
        
        response = requests.get(api_url, headers=headers, timeout=15)
        
        # Get the actual scrape result
        real_price = engine._scrape_real_competitor_price(offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'plid': plid,
            'api_url': api_url,
            'api_status_code': response.status_code,
            'api_response_length': len(response.text),
            'api_response_preview': response.text[:500] if response.text else 'empty',
            'real_scrape_result': real_price,
            'scrape_successful': real_price is not None and real_price != "we_own_buybox",
            'we_own_buybox': real_price == "we_own_buybox",
            'product_url': product_info.get('product_url')
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'offer_id': offer_id
        }), 500

@app.route('/debug-api-structure/<offer_id>')
def debug_api_structure(offer_id):
    """See the full API response structure to find price fields"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        product_info = engine.product_config.get(offer_id, {})
        plid = product_info.get('plid')
        
        if not plid:
            return jsonify({'error': 'No PLID found'})
        
        api_url = f"https://api.takealot.com/rest/v-1-0-0/product-details/{plid}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.takealot.com/{plid.lower()}",
        }
        
        response = requests.get(api_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract key sections
            product = data.get("product", {}) if "product" in data else data
            
            return jsonify({
                'offer_id': offer_id,
                'plid': plid,
                'top_level_keys': list(data.keys()),
                'product_keys': list(product.keys()) if product else [],
                'buybox': product.get('buybox'),
                'core': product.get('core'),
                'pricing': product.get('pricing'),
                'purchase_box': product.get('purchase_box'),
                'offers': product.get('offers'),
                'full_response': data  # Full response for inspection
            })
        else:
            return jsonify({
                'error': f'API returned {response.status_code}',
                'response': response.text[:1000]
            })
            
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/monitoring/status')
def monitoring_status():
    """Check monitoring system status"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        with sqlite3.connect("price_monitor.db") as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*), MAX(last_updated) FROM competitor_prices')
            result = cursor.fetchone()
            total_prices, last_updated = result
    except:
        total_prices, last_updated = 0, None
    
    return jsonify({
        'monitoring_active': engine.price_monitor.is_monitoring,
        'thread_alive': engine.price_monitor.monitoring_thread.is_alive() if engine.price_monitor.monitoring_thread else False,
        'total_products_configured': len(engine.product_config),
        'prices_in_database': total_prices,
        'last_price_update': last_updated,
        'monitoring_interval': '30 minutes',
        'estimated_cycle_time': f"{(len(engine.product_config) * 2) / 60:.1f} minutes"
    })

@app.route('/monitoring/prices')
def monitoring_prices():
    """Get recent stored competitor prices"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        with sqlite3.connect("price_monitor.db") as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT offer_id, competitor_price, last_updated, source 
                FROM competitor_prices 
                ORDER BY last_updated DESC 
                LIMIT 100
            ''')
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
            'count': len(prices),
            'showing': 'last 100 prices'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/my-price-history')
def my_price_history():
    """Get YOUR price change history"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        limit = request.args.get('limit', 100, type=int)
        offer_id = request.args.get('offer_id')  # Optional filter by product
        
        with sqlite3.connect("price_monitor.db") as conn:
            cursor = conn.cursor()
            
            if offer_id:
                cursor.execute('''
                    SELECT offer_id, old_price, new_price, competitor_price, reason, success, timestamp
                    FROM my_price_history 
                    WHERE offer_id = ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (offer_id, limit))
            else:
                cursor.execute('''
                    SELECT offer_id, old_price, new_price, competitor_price, reason, success, timestamp
                    FROM my_price_history 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,))
            
            results = cursor.fetchall()
        
        history = []
        for row in results:
            history.append({
                'offer_id': row[0],
                'old_price': row[1],
                'new_price': row[2],
                'competitor_price': row[3],
                'reason': row[4],
                'success': bool(row[5]),
                'timestamp': row[6],
                'price_change': row[2] - row[1]
            })
        
        return jsonify({
            'price_changes': history,
            'count': len(history),
            'showing': f'last {limit} price changes' + (f' for {offer_id}' if offer_id else '')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/monitoring/start')
def start_monitoring():
    """Manually start monitoring"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    engine.start_background_monitoring()
    return jsonify({
        'status': 'monitoring_started',
        'products': len(engine.product_config),
        'interval': '30 minutes'
    })

@app.route('/monitoring/stop')
def stop_monitoring():
    """Manually stop monitoring"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    engine.stop_monitoring()
    return jsonify({'status': 'monitoring_stopped'})

@app.route('/cache/clear')
def clear_cache():
    """Clear price cache"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    cache_size = len(engine.price_cache)
    engine.price_cache = {}
    
    return jsonify({
        'status': 'cache_cleared',
        'items_removed': cache_size,
        'message': 'All cached prices removed'
    })

@app.route('/cache/status')
def cache_status():
    """Check cache status"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    cached_items = []
    for offer_id, data in engine.price_cache.items():
        age_seconds = time.time() - data['timestamp']
        cached_items.append({
            'offer_id': offer_id,
            'price': data['price'],
            'age_minutes': round(age_seconds / 60, 1)
        })
    
    return jsonify({
        'total_cached': len(cached_items),
        'cached_items': cached_items[:50]  # Show first 50
    })

@app.route('/dashboard')
def dashboard():
    """Web dashboard for monitoring repricing engine"""
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Takealot Repricing Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            background: white;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        
        h1 {
            color: #333;
            font-size: 32px;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #666;
            font-size: 16px;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        
        .stat-card {
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .stat-label {
            color: #666;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
        }
        
        .stat-value {
            color: #333;
            font-size: 36px;
            font-weight: bold;
        }
        
        .status-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
            margin-top: 10px;
        }
        
        .status-active {
            background: #10b981;
            color: white;
        }
        
        .status-inactive {
            background: #ef4444;
            color: white;
        }
        
        .table-container {
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: #f3f4f6;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            font-size: 14px;
        }
        
        td {
            padding: 12px;
            border-bottom: 1px solid #e5e7eb;
            color: #4b5563;
        }
        
        tr:hover {
            background: #f9fafb;
        }
        
        .price {
            font-weight: 600;
            color: #059669;
        }
        
        .timestamp {
            color: #9ca3af;
            font-size: 13px;
        }
        
        .refresh-btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
            transition: background 0.3s;
        }
        
        .refresh-btn:hover {
            background: #5568d3;
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Takealot Repricing Dashboard</h1>
            <p class="subtitle">Real-time monitoring of your automated repricing engine</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Monitoring Status</div>
                <div class="stat-value" id="monitoring-status">Loading...</div>
            </div>
            
            <div class="stat-card">
                <div class="stat-label">Products Configured</div>
                <div class="stat-value" id="total-products">0</div>
            </div>
            
            <div class="stat-card">
                <div class="stat-label">Prices in Database</div>
                <div class="stat-value" id="prices-stored">0</div>
            </div>
            
            <div class="stat-card">
                <div class="stat-label">Last Updated</div>
                <div class="stat-value" style="font-size: 20px;" id="last-update">Never</div>
            </div>
        </div>
        
        <div class="table-container">
            <h2 style="margin-bottom: 20px; color: #333;">Recent Price Updates</h2>
            <div id="prices-table">
                <div class="loading">Loading recent prices...</div>
            </div>
            <button class="refresh-btn" onclick="loadData()">🔄 Refresh Data</button>
        </div>
    </div>
    
    <script>
        function formatTimestamp(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleString();
        }
        
        function formatTimeAgo(timestamp) {
            const now = new Date();
            const then = new Date(timestamp);
            const diffMs = now - then;
            const diffMins = Math.floor(diffMs / 60000);
            
            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins} min ago`;
            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return `${diffHours} hours ago`;
            const diffDays = Math.floor(diffHours / 24);
            return `${diffDays} days ago`;
        }
        
        async function loadData() {
            try {
                // Load monitoring status
                const statusRes = await fetch('/monitoring/status');
                const status = await statusRes.json();
                
                document.getElementById('monitoring-status').innerHTML = 
                    status.monitoring_active 
                    ? '<span class="status-badge status-active">● ACTIVE</span>'
                    : '<span class="status-badge status-inactive">● STOPPED</span>';
                
                document.getElementById('total-products').textContent = 
                    status.total_products_configured.toLocaleString();
                
                document.getElementById('prices-stored').textContent = 
                    status.prices_in_database.toLocaleString();
                
                document.getElementById('last-update').textContent = 
                    status.last_price_update ? formatTimeAgo(status.last_price_update) : 'Never';
                
                // Load recent prices
                const pricesRes = await fetch('/monitoring/prices');
                const prices = await pricesRes.json();
                
                if (prices.stored_prices.length === 0) {
                    document.getElementById('prices-table').innerHTML = 
                        '<div class="loading">No prices stored yet. Monitoring is running...</div>';
                } else {
                    let tableHtml = `
                        <table>
                            <thead>
                                <tr>
                                    <th>Offer ID</th>
                                    <th>Competitor Price</th>
                                    <th>Last Updated</th>
                                    <th>Source</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;
                    
                    prices.stored_prices.slice(0, 50).forEach(price => {
                        tableHtml += `
                            <tr>
                                <td><strong>${price.offer_id}</strong></td>
                                <td class="price">R ${price.competitor_price.toFixed(2)}</td>
                                <td class="timestamp">${formatTimestamp(price.last_updated)}</td>
                                <td>${price.source}</td>
                            </tr>
                        `;
                    });
                    
                    tableHtml += '</tbody></table>';
                    document.getElementById('prices-table').innerHTML = tableHtml;
                }
                
            } catch (error) {
                console.error('Error loading data:', error);
                document.getElementById('prices-table').innerHTML = 
                    '<div class="loading">Error loading data. Please refresh the page.</div>';
            }
        }
        
        // Load data on page load
        loadData();
        
        // Auto-refresh every 30 seconds
        setInterval(loadData, 30000);
    </script>
</body>
</html>
    '''
    return html

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting app on port {port}")
    logger.info(f"✅ App ready to receive requests")
    app.run(host='0.0.0.0', port=port, debug=False)
