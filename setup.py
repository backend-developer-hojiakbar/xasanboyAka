import os
import sys
import subprocess
from pathlib import Path

def check_python_version():
    """Check if Python version is 3.8 or higher"""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8 or higher is required")
        print(f"Current version: {sys.version}")
        return False
    print(f"✅ Python version: {sys.version}")
    return True

def install_requirements():
    """Install required packages"""
    print("📦 Installing required packages...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Packages installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing packages: {e}")
        return False

def check_env_file():
    """Check if .env file exists and has required variables"""
    env_path = Path(".env")
    if not env_path.exists():
        print("❌ .env file not found")
        print("Please create .env file with required configuration")
        return False
    
    required_vars = ["BOT_TOKEN", "ADMIN_ID"]
    missing_vars = []
    
    with open(env_path, 'r') as f:
        content = f.read()
        for var in required_vars:
            if var not in content or f"{var}=" in content.split('\n'):
                missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("Please update your .env file with all required variables")
        return False
    
    print("✅ .env file configuration OK")
    return True

def test_database():
    """Test database connection"""
    print("🔍 Testing database connection...")
    try:
        from src.models.database import init_database
        engine = init_database()
        print("✅ Database connection successful")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

def main():
    """Main setup function"""
    print("🚀 Auto Message Bot Setup")
    print("=" * 40)
    
    # Check Python version
    if not check_python_version():
        return
    
    # Install requirements
    if not install_requirements():
        return
    
    # Check environment configuration
    if not check_env_file():
        return
    
    # Test database
    if not test_database():
        return
    
    print("\n🎉 Setup completed successfully!")
    print("\nNext steps:")
    print("1. Make sure your BOT_TOKEN and ADMIN_ID are correct in .env")
    print("2. Run the bot with: python main.py")
    print("3. Test the bot by sending /start to your bot")

if __name__ == "__main__":
    main()