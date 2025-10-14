import os
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import requests
import time
import random
import hashlib
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

class TakealotRepricingEngine:
    def __init__(self):
        self.session = requests.Session()
        self.price_cache = {}
        self.cache_ttl = 3600  # 1 hour cache
        self.last_request_time = 0
        self.min_request_interval = 2.5
        
        # Load product configurations
        self.product_config = self._load_product_config()
        
        logger.info("🚀 Takealot Repricing Engine Initialized")

    def _load_product_config(self):  # ✅ moved left by 1 tab
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

    def get_competitor_price(self, offer_id):
        """Get competitor price with caching and rate limiting"""
        try:
            # Check cache first
            cached_price = self._get_cached_price(offer_id)
            if cached_price is not None:
                logger.info(f"💾 Using cached price for {offer_id}: R{cached_price}")
                return cached_price
            
            # Rate limiting
            self._respect_rate_limit()
            
            # Simulate web scraping (you'll implement actual scraping here)
            competitor_price = self._simulate_scraping(offer_id)
            
            # Cache the result
            self._cache_price(offer_id, competitor_price)
            logger.info(f"💰 Competitor price for {offer_id}: R{competitor_price}")
            
            return competitor_price
            
        except Exception as e:
            logger.error(f"❌ Failed to get competitor price: {e}")
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
        
        # RULE 1: No change if already matching
        if my_price == competitor_price:
            logger.info("   ✅ NO CHANGE: Already at optimal price")
            return my_price
        
        # RULE 2: If competitor below THIS PRODUCT'S cost, revert to THIS PRODUCT'S selling price
        if competitor_price < cost_price:
            logger.info(f"   🔄 REVERT: Competitor below product cost → R{selling_price}")
            return selling_price
        
        # RULE 3: Always be R1 below competitor (whole numbers)
        new_price = competitor_price - 1
        logger.info(f"   📉 ADJUST: R1 below → R{new_price}")
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
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()

    def _simulate_scraping(self, offer_id):
        time.sleep(1)  # Simulate scraping delay
        # Convert offer_id to string to handle both string and integer IDs
        offer_id_str = str(offer_id)
        hash_obj = hashlib.md5(offer_id_str.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        base_price = 450 + (hash_int % 200)  # Prices between 450-650 (WHOLE NUMBERS)
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

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Takealot Repricing Engine',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat(),
        'environment': os.getenv('RAILWAY_ENVIRONMENT', 'development')
    })

@app.route('/webhook/price-change', methods=['POST'])
def handle_price_change():
    """Main webhook endpoint for price changes"""
    try:
        webhook_data = request.get_json()
        logger.info(f"📥 Webhook received: {webhook_data}")
        
        offer_id = webhook_data.get('offer_id')
        
        # Extract YOUR current price from values_changed
        values_changed = webhook_data.get('values_changed', '{}')
        try:
            values_dict = json.loads(values_changed)
            # Get your NEW selling price from the webhook
            my_current_price = values_dict.get('selling_price', {}).get('new_value', 0)
        except:
            my_current_price = 0  # Fallback
        
        logger.info(f"💰 Extracted - Offer: {offer_id}, My Price: R{my_current_price}")
        
        if not offer_id:
            return jsonify({'error': 'Missing offer_id'}), 400
        
        # Get competitor price
        competitor_price = engine.get_competitor_price(offer_id)
        
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
            'calculated_price': optimal_price,
            'price_updated': update_success,
            'business_rule': describe_business_rule(my_current_price, competitor_price, optimal_price),
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"📤 Webhook response: {response}")
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/test/<offer_id>')
def test_endpoint(offer_id):
    """Test endpoint for manual testing"""
    try:
        test_price = 500  # Whole number now
        competitor_price = engine.get_competitor_price(offer_id)
        # Convert competitor price to whole number
        competitor_price = int(competitor_price) if competitor_price else 500
        optimal_price = engine.calculate_optimal_price(test_price, competitor_price, offer_id)
        
        return jsonify({
            'offer_id': offer_id,
            'test_price': test_price,
            'competitor_price': competitor_price,
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
        'service': 'Takealot Repricing Engine',
        'version': '1.0.0'
    })

def describe_business_rule(my_price, competitor_price, optimal_price):
    """Describe which business rule was applied"""
    # Get thresholds for the specific product (simplified for this function)
    COST_PRICE = 515  # Default fallback (WHOLE NUMBER)
    SELLING_PRICE = 714  # Default fallback (WHOLE NUMBER)
    
    my_price = int(my_price)
    competitor_price = int(competitor_price)
    optimal_price = int(optimal_price)
    
    if my_price == optimal_price:
        return "NO_CHANGE - Price already optimal"
    elif competitor_price < COST_PRICE:
        return f"REVERT_TO_SELLING - Competitor R{competitor_price} < Cost R{COST_PRICE}"
    else:
        return f"R1_BELOW - My price R{optimal_price} = Competitor R{competitor_price} - R1"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Takealot Repricing Engine on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)