from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, nullable=False)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    phone_number = Column(String)
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=False)
    subscription_end = Column(DateTime)
    # Session persistence fields
    session_data = Column(Text)  # JSON string to store session data
    last_activity = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    payments = relationship("Payment", back_populates="user")
    messages = relationship("ScheduledMessage", back_populates="user")
    groups = relationship("UserGroup", back_populates="user")
    folders = relationship("GroupFolder", back_populates="user")

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    amount = Column(String)
    card_number = Column(String)
    receipt_photo_id = Column(String)
    status = Column(String, default='pending')  # pending, approved, rejected
    admin_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="payments")

class ScheduledMessage(Base):
    __tablename__ = 'scheduled_messages'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    message_text = Column(Text)
    media_file_id = Column(String)
    media_type = Column(String)  # photo, video, document
    schedule_time = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    is_sent = Column(Boolean, default=False)
    is_repeat = Column(Boolean, default=False)  # New field for repeating messages
    repeat_interval = Column(Integer, default=5)  # New field for repeat interval in minutes
    target_groups = Column(Text)  # JSON string of group IDs
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="messages")

class UserGroup(Base):
    __tablename__ = 'user_groups'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    group_id = Column(String, nullable=False)
    group_title = Column(String)
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="groups")

class GroupFolder(Base):
    """User's saved group selections (folders)"""
    __tablename__ = 'group_folders'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    folder_name = Column(String, nullable=False)
    group_ids = Column(Text)  # JSON string of group IDs
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="folders")

class BotSettings(Base):
    __tablename__ = 'bot_settings'
    
    id = Column(Integer, primary_key=True)
    setting_name = Column(String, unique=True, nullable=False)
    setting_value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Database setup
def get_database_url():
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv('DATABASE_URL', 'sqlite:///bot_database.db')

# Global engine instance to prevent database locking
_engine = None
_Session = None

def init_database():
    global _engine
    if _engine is None:
        database_url = get_database_url()
        # Add timeout and check_same_thread for SQLite to prevent locking
        if database_url.startswith('sqlite'):
            _engine = create_engine(
                database_url, 
                pool_pre_ping=True,
                connect_args={'check_same_thread': False, 'timeout': 30}
            )
        else:
            _engine = create_engine(database_url, pool_pre_ping=True)
        Base.metadata.create_all(_engine)
    return _engine

def get_session():
    global _Session
    if _Session is None:
        engine = init_database()
        _Session = sessionmaker(bind=engine)
    return _Session()

# Helper functions for session persistence
import json

def save_user_session(user_id, session_data):
    """Save user session data to database"""
    db_session = get_session()
    try:
        user = db_session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            # Only save specific session data we need
            filtered_data = {
                'phone_number': session_data.get('phone_number'),
                'using_demo': session_data.get('using_demo'),
                'account_verified': session_data.get('account_verified'),
                'user_info': session_data.get('user_info')
            }
            user.session_data = json.dumps(filtered_data)
            user.last_activity = datetime.utcnow()
            db_session.commit()
            return True
    except Exception as e:
        print(f"Error saving session: {e}")
    finally:
        db_session.close()
    return False

def load_user_session(user_id):
    """Load user session data from database"""
    db_session = get_session()
    try:
        user = db_session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.session_data:
            session_data = json.loads(user.session_data)
            # Restore session data to context format
            restored_data = {}
            if 'phone_number' in session_data:
                restored_data['phone_number'] = session_data['phone_number']
            if 'using_demo' in session_data:
                restored_data['using_demo'] = session_data['using_demo']
            if 'account_verified' in session_data:
                restored_data['account_verified'] = session_data['account_verified']
            if 'user_info' in session_data:
                restored_data['user_info'] = session_data['user_info']
            return restored_data
    except Exception as e:
        print(f"Error loading session: {e}")
    finally:
        db_session.close()
    return {}

def clear_user_session(user_id):
    """Clear user session data from database"""
    db_session = get_session()
    try:
        user = db_session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            user.session_data = None
            user.last_activity = datetime.utcnow()
            db_session.commit()
            return True
    except Exception as e:
        print(f"Error clearing session: {e}")
    finally:
        db_session.close()
    return False