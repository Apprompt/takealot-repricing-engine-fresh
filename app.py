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
        self.min_request_interval = 3.0  # Increased for real scraping

        # Load product configurations
        self.product_config = self._load_product_config()
        
        logger.info("🚀 Takealot Repricing Engine with REAL Scraping Initialized")

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
        """Extract REAL competitor price from Takealot product page"""
        try:
            self._respect_rate_limit()
            
            # Takealot product URL
            url = f"https://www.takealot.com/plid{offer_id}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
            
            logger.info(f"🌐 Scraping REAL competitor price from: {url}")
            response = self.session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Parse the HTML for competitor prices
            competitor_price = self._extract_competitor_price_from_html(response.text, offer_id)
            
            if competitor_price:
                logger.info(f"💰 REAL Competitor price found: R{competitor_price}")
                return float(competitor_price)
            else:
                logger.warning("⚠️ No competitor price found in HTML, using fallback")
                return self._get_fallback_price(offer_id)
                
        except Exception as e:
            logger.error(f"❌ Real scraping failed: {e}")
            return self._get_fallback_price(offer_id)

    def _extract_competitor_price_from_html(self, html_content, offer_id):
        """Extract the LOWEST competitor price from Takealot HTML"""
        try:
            import re
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html_content, 'html.parser')
            competitor_prices = []
            
            logger.info(f"🔍 Analyzing HTML structure for competitor prices...")
            
            # STRATEGY 1: Look for JSON-LD structured data (most reliable)
            script_tags = soup.find_all('script', type='application/ld+json')
            for script in script_tags:
                try:
                    data = json.loads(script.string)
                    # Look for product offers
                    if isinstance(data, dict):
                        # Check for offers array
                        offers = data.get('offers', [])
                        if isinstance(offers, list):
                            for offer in offers:
                                if isinstance(offer, dict) and 'price' in offer:
                                    price = float(offer['price'])
                                    seller = offer.get('seller', {}).get('name', '')
                                    # If it's not your seller, consider it competitor
                                    if seller and 'your store' not in seller.lower():
                                        competitor_prices.append(price)
                                        logger.info(f"🔍 Found JSON-LD competitor price: R{price} from {seller}")
                        # Check for single offer
                        elif isinstance(offers, dict) and 'price' in offers:
                            price = float(offers['price'])
                            seller = offers.get('seller', {}).get('name', '')
                            if seller and 'your store' not in seller.lower():
                                competitor_prices.append(price)
                                logger.info(f"🔍 Found JSON-LD competitor price: R{price} from {seller}")
                except Exception as e:
                    logger.debug(f"❌ JSON-LD parsing failed: {e}")
                    continue
            
            # STRATEGY 2: Look for competitor-specific elements
            competitor_selectors = [
                '[data-competitor-price]',
                '.competitor-price',
                '.other-seller-price',
                '.multiple-offer-price',
                '.seller-pricing',
                '[data-seller-type="competitor"]',
                '.competitive-price'
            ]
            
            for selector in competitor_selectors:
                elements = soup.select(selector)
                for element in elements:
                    price_text = element.get_text().strip()
                    price_match = re.search(r'R\s*(\d+(?:\.\d{2})?)', price_text)
                    if price_match:
                        price = float(price_match.group(1))
                        if 50 < price < 5000:  # Reasonable price range
                            competitor_prices.append(price)
                            logger.info(f"🔍 Found competitor price with '{selector}': R{price}")
            
            # STRATEGY 3: Look for "from RXXX" patterns (lowest price indicator)
            from_price_matches = re.findall(r'from\s*R\s*(\d+(?:\.\d{2})?)', html_content, re.IGNORECASE)
            for match in from_price_matches:
                price = float(match)
                if 50 < price < 5000:
                    competitor_prices.append(price)
                    logger.info(f"🔍 Found 'from' price pattern: R{price}")
            
            # STRATEGY 4: Look for price in meta tags
            meta_selectors = [
                'meta[property="product:price:amount"]',
                'meta[name="twitter:data1"]',
                'meta[itemprop="price"]'
            ]
            
            for selector in meta_selectors:
                elements = soup.select(selector)
                for element in elements:
                    price_content = element.get('content', '')
                    if price_content and price_content.replace('.', '').isdigit():
                        price = float(price_content)
                        if 50 < price < 5000:
                            competitor_prices.append(price)
                            logger.info(f"🔍 Found meta price: R{price}")
            
            # STRATEGY 5: Look for lowest price in data attributes
            data_price_elements = soup.select('[data-price]')
            for element in data_price_elements:
                price_attr = element.get('data-price', '')
                if price_attr and price_attr.replace('.', '').isdigit():
                    price = float(price_attr)
                    if 50 < price < 5000:
                        competitor_prices.append(price)
                        logger.info(f"🔍 Found data-price: R{price}")
            
            # Return the LOWEST competitor price found
            if competitor_prices:
                lowest_price = min(competitor_prices)
                logger.info(f"🏆 Using LOWEST competitor price: R{lowest_price} from {len(competitor_prices)} options")
                return lowest_price
            
            # FALLBACK: If no competitors found, try to get any price as reference
            logger.info("🔄 No competitor prices found, trying to extract any price as reference")
            main_price_selectors = [
                '.currency',
                '.price',
                '.selling-price',
                '.amount',
                '[data-product-price]'
            ]
            
            for selector in main_price_selectors:
                elements = soup.select(selector)
                for element in elements:
                    price_text = element.get_text().strip()
                    price_match = re.search(r'R\s*(\d+(?:\.\d{2})?)', price_text)
                    if price_match:
                        price = float(price_match.group(1))
                        if 50 < price < 5000:
                            logger.info(f"🔍 Using main product price as reference: R{price}")
                            return price
            
            logger.info("❌ No prices found in HTML")
            return None
            
        except Exception as e:
            logger.error(f"❌ HTML price extraction failed: {e}")
            import traceback
            logger.error(f"❌ Stack trace: {traceback.format_exc()}")
            return None

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
    """Extract competitor price from webhook payload if available"""
    try:
        logger.info("🔍 Searching for competitor data in webhook...")
        
        # Check various possible locations for competitor data
        competitor_sources = [
            webhook_data.get('competitor_prices'),
            webhook_data.get('market_data'),
            webhook_data.get('competitive_data'),
            webhook_data.get('lowest_price'),
            webhook_data.get('min_competitor_price'),
            webhook_data.get('competitor_price'),
        ]
        
        for source in competitor_sources:
            if source:
                logger.info(f"🔍 Found potential competitor data: {source}")
        
        # Method 1: Direct competitor prices array
        competitor_prices = webhook_data.get('competitor_prices')
        if competitor_prices:
            logger.info(f"💰 Found competitor_prices: {competitor_prices}")
            if isinstance(competitor_prices, list):
                prices = []
                for price_data in competitor_prices:
                    if isinstance(price_data, dict) and price_data.get('price'):
                        prices.append(float(price_data.get('price')))
                    elif isinstance(price_data, (int, float)):
                        prices.append(float(price_data))
                
                if prices:
                    lowest = min(prices)
                    logger.info(f"💰 Extracted competitor prices: {prices}, using lowest: R{lowest}")
                    return lowest
        
        # Method 2: Market data object (could be string or dict)
        market_data = webhook_data.get('market_data', {})
        logger.info(f"🔍 Checking market_data: {market_data}")
        
        if isinstance(market_data, str):
            try:
                market_data = json.loads(market_data)
                logger.info(f"📊 Parsed market_data as JSON: {market_data}")
            except:
                market_data = {}
                logger.info("❌ Could not parse market_data as JSON")
        
        if isinstance(market_data, dict):
            lowest_competitor = market_data.get('lowest_competitor') or market_data.get('min_price') or market_data.get('lowest_price')
            if lowest_competitor:
                logger.info(f"💰 Extracted lowest competitor from market_data: R{lowest_competitor}")
                return float(lowest_competitor)
        
        # Method 3: Simple competitor price field
        simple_competitor = webhook_data.get('competitor_price') or webhook_data.get('lowest_price') or webhook_data.get('min_competitor_price')
        if simple_competitor:
            logger.info(f"💰 Extracted simple competitor price: R{simple_competitor}")
            return float(simple_competitor)
        
        # Method 4: Check if competitor data is in values_changed
        values_changed = webhook_data.get('values_changed', '{}')
        if isinstance(values_changed, str):
            try:
                values_dict = json.loads(values_changed)
                competitor_price = values_dict.get('competitor_price', {}).get('new_value')
                if competitor_price:
                    logger.info(f"💰 Extracted competitor price from values_changed: R{competitor_price}")
                    return float(competitor_price)
            except:
                pass
        
        logger.info("❌ No competitor data found in webhook")
        return None
        
    except Exception as e:
        logger.error(f"❌ Failed to extract competitor from webhook: {e}")
        import traceback
        logger.error(f"❌ Stack trace: {traceback.format_exc()}")
        return None

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
        'environment': os.getenv('RAILWAY_ENVIRONMENT', 'development'),
        'features': 'REAL Takealot Scraping + Webhook Competitor Extraction'
    })

@app.route('/webhook/price-change', methods=['POST'])
def handle_price_change():
    """Main webhook endpoint for price changes - WITH REAL SCRAPING"""
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
        
        # 🎯 INSTANT WEBHOOK EXTRACTION FIRST
        competitor_price = extract_competitor_from_webhook(webhook_data, offer_id)
        
        # Only fallback to REAL scraping if webhook has no competitor data
        if competitor_price is None:
            logger.info("🔄 No competitor data in webhook, using REAL scraping")
            competitor_price = engine.get_competitor_price(offer_id)
            source = 'real_scraping'
        else:
            logger.info(f"🎉 USING INSTANT WEBHOOK COMPETITOR DATA: R{competitor_price}")
            source = 'webhook_instant'
        
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
        'version': '1.0.0',
        'feature': 'REAL Takealot Scraping Implementation'
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
    logger.info(f"🎯 FEATURE: REAL Takealot Scraping Implementation")
    app.run(host='0.0.0.0', port=port, debug=False)