import os
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import requests
import time
import random
import hashlib

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
        self.cache_ttl = 3600
        self.last_request_time = 0
        self.min_request_interval = 2.5
        
        # Your business logic constants
        self.COST_PRICE = 515.00
        self.SELLING_PRICE = 714.00
        
        logger.info("🚀 Takealot Repricing Engine Initialized")

    def get_competitor_price(self, offer_id):
        """Get competitor price with caching and rate limiting"""
        try:
            cached_price = self._get_cached_price(offer_id)
            if cached_price is not None:
                logger.info(f"💾 Using cached price for {offer_id}: R{cached_price:.2f}")
                return cached_price
            
            self._respect_rate_limit()
            competitor_price = self._simulate_scraping(offer_id)
            self._cache_price(offer_id, competitor_price)
            logger.info(f"💰 Competitor price for {offer_id}: R{competitor_price:.2f}")
            return competitor_price
            
        except Exception as e:
            logger.error(f"❌ Failed to get competitor price: {e}")
            return self._get_fallback_price(offer_id)

    def calculate_optimal_price(self, my_price, competitor_price, offer_id):
        """YOUR EXACT BUSINESS LOGIC"""
        logger.info(f"🧮 Calculating price for {offer_id}")
        logger.info(f"   My price: R{my_price:.2f}, Competitor: R{competitor_price:.2f}")
        
        # Rule 1: No change if already matching
        if abs(my_price - competitor_price) < 0.01:
            logger.info("   ✅ NO CHANGE: Already at optimal price")
            return my_price
        
        # Rule 2: If competitor below cost, revert to selling price
        if competitor_price < self.COST_PRICE:
            logger.info(f"   🔄 REVERT: Competitor below cost → R{self.SELLING_PRICE:.2f}")
            return self.SELLING_PRICE
        
        # Rule 3: Always R1.00 below competitor
        new_price = competitor_price - 1.00
        logger.info(f"   📉 ADJUST: R1.00 below → R{new_price:.2f}")
        return new_price

    def update_price(self, offer_id, new_price):
        """Update price on Takealot (simulated)"""
        try:
            logger.info(f"📤 Updating {offer_id} to R{new_price:.2f}")
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"❌ Price update failed: {e}")
            return False

    def _get_cached_price(self, offer_id):
        if offer_id in self.price_cache:
            cached_time, price = self.price_cache[offer_id]
            if time.time() - cached_time < self.cache_ttl:
                return price
        return None

    def _cache_price(self, offer_id, price):
        self.price_cache[offer_id] = (time.time(), price)

    def _respect_rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last + random.uniform(0.5, 1.5)
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def _simulate_scraping(self, offer_id):
        time.sleep(1)
        hash_obj = hashlib.md5(offer_id.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        base_price = 450 + (hash_int % 200)
        return float(base_price)

    def _get_fallback_price(self, offer_id):
        hash_obj = hashlib.md5(offer_id.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        fallback_price = 500 + (hash_int % 100)
        logger.warning(f"🔄 Using fallback price: R{fallback_price:.2f}")
        return float(fallback_price)

# Initialize the engine
engine = TakealotRepricingEngine()

@app.route('/')
def home():
    return jsonify({
        'status': 'healthy',
        'service': 'Takealot Repricing Engine',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat(),
        'environment': os.getenv('RAILWAY_ENVIRONMENT', 'development')
    })

@app.route('/webhook/price-change', methods=['POST'])
def handle_price_change():
    try:
        webhook_data = request.get_json()
        logger.info(f"📥 Webhook received: {webhook_data}")
        
        offer_id = webhook_data.get('offer_id')
        my_current_price = float(webhook_data.get('my_current_price', 0))
        
        if not offer_id:
            return jsonify({'error': 'Missing offer_id'}), 400
        
        competitor_price = engine.get_competitor_price(offer_id)
        optimal_price = engine.calculate_optimal_price(my_current_price, competitor_price, offer_id)
        needs_update = abs(optimal_price - my_current_price) > 0.01
        
        if needs_update:
            update_success = engine.update_price(offer_id, optimal_price)
            status = 'updated' if update_success else 'update_failed'
        else:
            update_success = False
            status = 'no_change'
        
        response = {
            'status': status,
            'offer_id': offer_id,
            'your_current_price': my_current_price,
            'competitor_price': competitor_price,
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
    try:
        test_price = 500.00
        competitor_price = engine.get_competitor_price(offer_id)
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
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Takealot Repricing Engine',
        'version': '1.0.0'
    })

def describe_business_rule(my_price, competitor_price, optimal_price):
    COST_PRICE = 515.00
    SELLING_PRICE = 714.00
    
    if abs(my_price - optimal_price) < 0.01:
        return "NO_CHANGE - Price already optimal"
    elif competitor_price < COST_PRICE:
        return f"REVERT_TO_SELLING - Competitor R{competitor_price:.2f} < Cost R{COST_PRICE:.2f}"
    else:
        return f"R1_BELOW - My price R{optimal_price:.2f} = Competitor R{competitor_price:.2f} - R1.00"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Takealot Repricing Engine on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)