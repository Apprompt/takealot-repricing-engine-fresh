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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

class PriceMonitor:
    def __init__(self):
        self.db_file = "price_monitor.db"
        self._init_database()
        self.monitoring_thread = None
        self.is_monitoring = False
        
    def _init_database(self):
        """Initialize SQLite database for price storage"""
        conn = sqlite3.connect(self.db_file)
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
        conn.close()
        logger.info("✅ Price monitoring database initialized")
    
    def store_competitor_price(self, offer_id, price, source="scraping"):
        """Store competitor price in database"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO competitor_prices 
                (offer_id, competitor_price, last_updated, source)
                VALUES (?, ?, ?, ?)
            ''', (str(offer_id), price, datetime.now().isoformat(), source))
            conn.commit()
            conn.close()
            logger.info(f"💾 Stored competitor price for {offer_id}: R{price}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to store price: {e}")
            return False
    
    def get_competitor_price(self, offer_id):
        """Get stored competitor price (INSTANT)"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT competitor_price, last_updated, source 
                FROM competitor_prices 
                WHERE offer_id = ?
            ''', (str(offer_id),))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                price, last_updated, source = result
                # Check if data is fresh (less than 1 hour old)
                try:
                    last_time = datetime.fromisoformat(last_updated)
                    time_diff = (datetime.now() - last_time).total_seconds()
                    
                    if time_diff < 3600:  # 1 hour freshness
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

    def start_monitoring(self, product_list, interval_minutes=30):
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
                        # Use existing scraping method to get competitor price
                        competitor_price = engine.get_competitor_price(offer_id)
                        if competitor_price and competitor_price > 0:
                            self.store_competitor_price(offer_id, competitor_price, "background_monitor")
                        # Be nice to Takealot's servers
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"❌ Monitoring failed for {offer_id}: {e}")
                        time.sleep(5)  # Longer delay on error
                
                if self.is_monitoring:
                    logger.info(f"⏰ Monitoring cycle completed. Sleeping for {interval_minutes} minutes")
                    time.sleep(interval_minutes * 60)  # Convert to seconds
                
            except Exception as e:
                logger.error(f"❌ Monitoring loop error: {e}")
                if self.is_monitoring:
                    time.sleep(60)  # Wait a minute before retrying
    
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
        self.min_request_interval = 3.0

        # Load product configurations
        self.product_config = self._load_product_config()
        
        # Initialize price monitor
        self.price_monitor = PriceMonitor()
        
        logger.info("🚀 Takealot Repricing Engine with PROACTIVE MONITORING Initialized")

    def _load_product_config(self):
        """Load product config with comprehensive debugging"""
        try:
            current_dir = os.getcwd()
            logger.info(f"🔍 DEBUG: Current working directory: {current_dir}")

            # List all files in current directory
            try:
                files = os.listdir('.')
                logger.info(f"📁 Files in directory ({len(files)} total): {files}")
            except Exception as e:
                logger.error(f"❌ Cannot list directory: {e}")

            file_path = 'products_config.csv'
            logger.info(f"🔍 Looking for: {file_path}")

            if os.path.exists(file_path):
                df = pd.read_csv(file_path)
                
                # ✅ Optional safety check
                expected_cols = {"OfferID", "SellingPrice", "CostPrice"}
                missing = expected_cols - set(df.columns)
                if missing:
                    logger.error(f"❌ Missing columns in CSV: {missing}")
                    return {}
                
                logger.info(f"✅ Loaded CSV successfully with {len(df)} rows and columns {list(df.columns)}")

                config_dict = {
                    str(row["OfferID"]): {
                        "selling_price": int(row["SellingPrice"]),
                        "cost_price": int(row["CostPrice"])
                    }
                    for _, row in df.iterrows()
                }

                logger.info(f"🎉 SUCCESS: Loaded {len(config_dict)} products into config")
                logger.info(f"🧾 All configured Offer IDs: {list(config_dict.keys())[:10]}")

                return config_dict
            else:
                logger.error("❌ CRITICAL: products_config.csv NOT FOUND in deployment!")
                return {}
        except Exception as e:
            logger.error(f"❌ CRITICAL ERROR loading product config: {e}")
            import traceback
            logger.error(f"❌ Stack trace: {traceback.format_exc()}")
            return {}

    def start_background_monitoring(self):
        """Start monitoring all configured products"""
        product_list = list(self.product_config.keys())
        if product_list:
            self.price_monitor.start_monitoring(product_list, interval_minutes=30)
        else:
            logger.warning("⚠️ No products configured for monitoring")

    def get_product_thresholds(self, offer_id):
        """Get cost_price and selling_price for specific product"""
        # Convert to string for lookup (since CSV keys are strings)
        offer_id_str = str(offer_id)

        if offer_id_str in self.product_config:
            config = self.product_config[offer_id_str]
            logger.info(f"✅ Found config for {offer_id_str}: cost R{config.get('cost_price')}, selling R{config.get('selling_price')}")
            return config.get('cost_price'), config.get('selling_price')
        else:
            logger.warning(f"⚠️ No configuration found for '{offer_id_str}' - using fallback R500/R700")
            # Log first few product IDs for debugging
            sample_ids = list(self.product_config.keys())[:3]
            logger.info(f"📋 Sample configured IDs: {sample_ids}")
            return 500, 700  # Fallback values (WHOLE NUMBERS)

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
        """Get competitor price - try real scraping first, then fallbacks"""
        try:
            # Check cache first
            cached_price = self._get_cached_price(offer_id)
            if cached_price is not None:
                logger.info(f"💾 Using cached price for {offer_id}: R{cached_price}")
                return cached_price
            
            # Try REAL scraping first
            logger.info(f"🎯 Attempting REAL competitor price scraping for {offer_id}")
            real_price = self.get_real_competitor_price(offer_id)
            
            # If real scraping returned a valid price, use it
            if real_price and real_price > 0:
                self._cache_price(offer_id, real_price)
                return real_price
            else:
                # Fallback to simulated scraping
                logger.info("🔄 Real scraping failed, using simulated data")
                simulated_price = self._simulate_scraping(offer_id)
                self._cache_price(offer_id, simulated_price)
                return simulated_price
            
        except Exception as e:
            logger.error(f"❌ All competitor price methods failed: {e}")
            return self._get_fallback_price(offer_id)

    def get_real_competitor_price(self, offer_id):
        """Fetch lowest competitor price directly from Takealot's JSON API"""
        try:
            self._respect_rate_limit()
            api_url = f"https://api.takealot.com/rest/v-2-0-0/product-details/PLID{offer_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json",
            }
            logger.info(f"🌐 Fetching Takealot API: {api_url}")

            response = self.session.get(api_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            product = data.get("product", {})
            offers = product.get("offers", []) or product.get("offerList", [])
            prices = []

            for offer in offers:
                price_cents = offer.get("price")
                if isinstance(price_cents, (int, float)) and price_cents > 0:
                    prices.append(price_cents / 100.0)

            # Include buybox or main selling price if missing
            buybox_price = product.get("buybox", {}).get("price") or product.get("sellingPrice")
            if buybox_price:
                prices.append(buybox_price / 100.0)

            if prices:
                lowest = round(min(prices), 2)
                logger.info(f"🏆 Lowest competitor price from JSON API: R{lowest}")
                return lowest
            else:
                logger.warning("⚠️ No valid prices found in JSON API response")
                return self._get_fallback_price(offer_id)

        except Exception as e:
            logger.error(f"❌ Failed to fetch from JSON API: {e}")
            return self._get_fallback_price(offer_id)

    def calculate_optimal_price(self, my_price, competitor_price, offer_id):
        """YOUR BUSINESS LOGIC with product-specific thresholds - WHOLE NUMBERS ONLY"""
        # Get thresholds for THIS specific product
        cost_price, selling_price = self.get_product_thresholds(offer_id)
        
        # Convert to integers (whole numbers) for Takealot
        my_price = int(my_price)
        competitor_price = int(competitor_price)
        cost_price = int(cost_price)
        selling_price = int(selling_price)
        
        logger.info(f"🧮 Calculating price for {offer_id}")
        logger.info(f"   My price: R{my_price}, Competitor: R{competitor_price}")
        logger.info(f"   Product Cost: R{cost_price}, Product Selling: R{selling_price}")
        
        # RULE 1: If competitor below THIS PRODUCT'S cost, revert to THIS PRODUCT'S selling price
        if competitor_price < cost_price:
            logger.info(f"   🔄 REVERT: Competitor R{competitor_price} below product cost R{cost_price} → R{selling_price}")
            return selling_price
        
        # RULE 2: Always be R1 below competitor (whole numbers)
        new_price = competitor_price - 1
        logger.info(f"   📉 ADJUST: R1 below competitor R{competitor_price} → R{new_price}")
        return new_price

    def update_price(self, offer_id, new_price):
        """Update price on Takealot (simulated)"""
        try:
            logger.info(f"📤 Updating {offer_id} to R{new_price}")
            # Simulate API call
            time.sleep(0.5)
            return True
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

def extract_competitor_from_webhook(webhook_data, offer_id):
    """REALITY CHECK: Takealot webhooks don't contain competitor data"""
    logger.info("🔍 REALITY: Takealot webhooks typically don't contain competitor prices")
    logger.info(f"📋 Webhook only contains: {list(webhook_data.keys())}")
    
    # The truth: You'll almost always get None here
    return None

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
    """Main webhook endpoint - NOW WITH INSTANT PRICING"""
    try:
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
            update_success = engine.update_price(offer_id, optimal_price)
            status = 'updated' if update_success else 'update_failed'
        else:
            update_success = False
            status = 'no_change'
        
        response = {
            'status': status,
            'offer_id': offer_id,
            'your_current_price': int(my_current_price),
            'competitor_price': int(competitor_price),
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
        # Convert competitor price to whole number
        competitor_price = int(competitor_price) if competitor_price else 500
        optimal_price = engine.calculate_optimal_price(test_price, competitor_price, offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'test_price': test_price,
            'competitor_price': competitor_price,
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
            'price_difference': real_price - mock_price if real_price else None,
            'using_real_data': real_price != mock_price if real_price else False,
            'real_data_available': real_price is not None
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
        conn = sqlite3.connect("price_monitor.db")
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM competitor_prices ORDER BY last_updated DESC')
        results = cursor.fetchall()
        conn.close()
        
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

def describe_business_rule(my_price, competitor_price, optimal_price):
    """Describe which business rule was applied"""
    # Get thresholds for the specific product (simplified for this function)
    COST_PRICE = 515  # Default fallback (WHOLE NUMBER)
    SELLING_PRICE = 714  # Default fallback (WHOLE NUMBER)
    
    my_price = int(my_price)
    competitor_price = int(competitor_price)
    optimal_price = int(optimal_price)
    
    if competitor_price < COST_PRICE:
        return f"REVERT_TO_SELLING - Competitor R{competitor_price} < Cost R{COST_PRICE}"
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