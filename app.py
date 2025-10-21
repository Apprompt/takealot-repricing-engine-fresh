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

    def _extract_plid_from_url(self, product_url):
        """Extract PLID from Takealot URL"""
        try:
            plid_match = re.search(r'PLID(\d+)', product_url, re.IGNORECASE)
            if plid_match:
                plid = f"PLID{plid_match.group(1)}"
                logger.debug(f"✅ Extracted PLID: {plid}")
                return plid
            
            logger.warning(f"⚠️ Could not extract PLID from URL: {product_url}")
            return None
            
        except Exception as e:
            logger.error(f"❌ URL parsing failed for {product_url}: {e}")
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
        """Get min_price and max_price for specific product"""
        offer_id_str = str(offer_id)

        if offer_id_str in self.product_config:
            config = self.product_config[offer_id_str]
            return config.get('min_price', 500), config.get('max_price', 700)
        else:
            logger.warning(f"⚠️ No config for '{offer_id_str}' - using fallback")
            return 500, 700

    def get_competitor_price_instant(self, offer_id):
        """INSTANT competitor price lookup"""
        stored_price = self.price_monitor.get_competitor_price(offer_id)
        if stored_price is not None:
            return stored_price, 'proactive_monitoring'
        
        logger.info("🔄 No stored price, using real-time scraping")
        real_time_price = self.get_competitor_price(offer_id)
        return real_time_price, 'real_time_scraping'

    def get_competitor_price(self, offer_id):
        """Get competitor price with fallback"""
        try:
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
            
            # 3. Try REAL scraping
            logger.info(f"🌐 Attempting REAL scraping for {offer_id}")
            real_price = self._scrape_real_competitor_price(offer_id)
            
            if real_price and real_price > 0:
                self._cache_price(offer_id, real_price)
                # Also store in database for future use
                self.price_monitor.store_competitor_price(offer_id, real_price, "real_time_scraping")
                return real_price
            elif real_price == "we_own_buybox":
                return "we_own_buybox"
            
            # 4. Fallback to simulation
            logger.warning(f"⚠️ Real scraping failed for {offer_id}, using simulation")
            simulated_price = self._simulate_scraping(offer_id)
            self._cache_price(offer_id, simulated_price)
            return simulated_price
            
        except Exception as e:
            logger.error(f"❌ All price methods failed: {e}")
            return self._get_fallback_price(offer_id)

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
            product = data.get("product", {})
            
            # Extract prices from multiple locations
            price_candidates = []
            
            # Method 1: Buybox price
            buybox = product.get("buybox", {})
            if buybox:
                buybox_price = buybox.get("price")
                if buybox_price and buybox_price > 0:
                    price_rand = buybox_price / 100.0
                    price_candidates.append(price_rand)
                    logger.info(f"💰 Buybox price: R{price_rand}")
                
                # Check if we own the buybox
                seller_id = buybox.get("seller_id") or buybox.get("seller_name", "")
                if seller_id and (str(seller_id) == "29844311" or "allbats" in str(seller_id).lower()):
                    logger.info("🎉 WE OWN THE BUYBOX!")
                    return "we_own_buybox"
            
            # Method 2: Core price
            core = product.get("core", {})
            if core:
                core_price = core.get("price") or core.get("current_price")
                if isinstance(core_price, dict):
                    selling_price = core_price.get("selling_price") or core_price.get("amount")
                    if selling_price and selling_price > 0:
                        price_rand = selling_price / 100.0
                        price_candidates.append(price_rand)
                        logger.info(f"💰 Core price: R{price_rand}")
                elif core_price and core_price > 0:
                    price_rand = core_price / 100.0
                    price_candidates.append(price_rand)
                    logger.info(f"💰 Core price: R{price_rand}")
            
            # Method 3: Direct price fields
            for field in ["price", "selling_price", "current_price"]:
                price_val = product.get(field)
                if price_val and price_val > 0:
                    price_rand = price_val / 100.0
                    price_candidates.append(price_rand)
                    logger.info(f"💰 {field}: R{price_rand}")
            
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
        
        # Extract current price
        values_changed = webhook_data.get('values_changed', '{}')
        my_current_price = 500  # Default
        
        try:
            if isinstance(values_changed, str):
                values_dict = json.loads(values_changed)
            else:
                values_dict = values_changed
            
            my_current_price = values_dict.get('selling_price', {}).get('new_value', 500)
        except:
            pass
        
        # Get competitor price
        competitor_price, source = engine.get_competitor_price_instant(offer_id)
        
        # Calculate optimal price
        optimal_price = engine.calculate_optimal_price(my_current_price, competitor_price, offer_id)
        
        # Update if needed
        needs_update = optimal_price != my_current_price
        update_success = False
        
        if needs_update:
            update_success = engine.update_price(offer_id, optimal_price)
        
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
    """Test real scraping for a product"""
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500
    
    try:
        logger.info(f"🔍 Testing real scrape for {offer_id}")
        real_price = engine._scrape_real_competitor_price(offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'real_scrape_result': real_price,
            'scrape_successful': real_price is not None and real_price != "we_own_buybox",
            'we_own_buybox': real_price == "we_own_buybox",
            'product_url': engine.product_config.get(offer_id, {}).get('product_url'),
            'plid': engine.product_config.get(offer_id, {}).get('plid')
        })
    except Exception as e:
        return jsonify({'error': str(e), 'offer_id': offer_id}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting app on port {port}")
    logger.info(f"✅ App ready to receive requests")
    app.run(host='0.0.0.0', port=port, debug=False)