import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Configure logging with ASCII-safe encoding for Windows
def setup_logging():
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler(f'{log_dir}/bot.log', encoding='utf-8')
    
    # Stream handler with error handling for Unicode
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    
    # Custom formatter that handles Unicode safely
    class ASCIISafeFormatter(logging.Formatter):
        def format(self, record):
            msg = super().format(record)
            # Replace non-ASCII characters for Windows console compatibility
            try:
                # Test if it can be encoded
                msg.encode('cp1251')
                return msg
            except UnicodeEncodeError:
                # Replace problematic characters
                return msg.encode('ascii', 'replace').decode('ascii')
    
    formatter = ASCIISafeFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, stream_handler]
    )
    return logging.getLogger(__name__)

def get_env_var(var_name, default=None):
    """Get environment variable with default fallback"""
    return os.getenv(var_name, default)

def is_admin(user_id):
    """Check if user is admin"""
    admin_id = get_env_var('ADMIN_ID')
    return str(user_id) == str(admin_id) if admin_id else False

def format_datetime(dt):
    """Format datetime for display"""
    if dt:
        return dt.strftime('%Y-%m-%d %H:%M')
    return 'Mavjud emas'

def format_subscription_status(user):
    """Format subscription status for display"""
    if not user.is_active:
        return "❌ Faol emas"
    
    if user.subscription_end:
        if user.subscription_end > datetime.utcnow():
            days_left = (user.subscription_end - datetime.utcnow()).days
            return f"✅ Faol ({days_left} kun qoldi)"
        else:
            return "❌ Muddati o'tgan"
    return "✅ Faol"

def parse_time_input(time_str):
    """Parse time input from user (e.g., '5' for 5 minutes)"""
    try:
        minutes = int(time_str)
        if minutes < int(get_env_var('MIN_SCHEDULE_TIME', 5)):
            return None, f"Minimal vaqt {get_env_var('MIN_SCHEDULE_TIME', 5)} daqiqadir"
        return datetime.utcnow() + timedelta(minutes=minutes), None
    except ValueError:
        return None, "Vaqt formati noto'g'ri"

def format_card_number(card_number):
    """Format card number for display (mask middle digits)"""
    if len(card_number) >= 16:
        return f"{card_number[:4]} **** **** {card_number[-4:]}"
    return card_number

def save_user_groups(user_id, groups_data):
    """Save user groups as JSON string"""
    return json.dumps(groups_data)

def load_user_groups(groups_json):
    """Load user groups from JSON string"""
    if not groups_json:
        return []
    try:
        return json.loads(groups_json)
    except json.JSONDecodeError:
        return []

def get_payment_card_details():
    """Get payment card details from environment"""
    card_number = get_env_var('CARD_NUMBER', '1234 5678 9012 3456')
    card_holder = get_env_var('CARD_HOLDER', 'Karta Ega Ismi')
    return card_number, card_holder

def check_subscription(user):
    """Check if user has active subscription"""
    if not user.is_active:
        return False
    if user.subscription_end and user.subscription_end < datetime.utcnow():
        return False
    return True