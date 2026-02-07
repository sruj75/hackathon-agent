"""
Firestore client setup for Firebase Admin SDK.

Provides async-compatible Firestore client for database operations.
"""
import os
import json
import logging
from typing import Optional
import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

# Global Firestore client instance
_firestore_client = None


def initialize_firestore():
    """
    Initialize Firebase Admin SDK and return Firestore client.
    
    Supports two authentication methods:
    1. Service account JSON file (local development)
    2. Service account JSON from environment variable (production)
    
    Environment Variables:
        FIREBASE_SERVICE_ACCOUNT_PATH: Path to service account JSON file
        FIREBASE_CREDENTIALS: Base64-encoded service account JSON
    
    Returns:
        Firestore client instance
    """
    global _firestore_client
    
    if _firestore_client is not None:
        return _firestore_client
    
    try:
        # Check if Firebase is already initialized
        if not firebase_admin._apps:
            # Method 1: Load from file path (local development)
            service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
            
            if service_account_path and os.path.exists(service_account_path):
                logger.info(f"Initializing Firebase from service account file: {service_account_path}")
                cred = credentials.Certificate(service_account_path)
                firebase_admin.initialize_app(cred)
            
            # Method 2: Load from environment variable (production - Render)
            elif os.getenv("FIREBASE_CREDENTIALS"):
                logger.info("Initializing Firebase from FIREBASE_CREDENTIALS environment variable")
                credentials_json = os.getenv("FIREBASE_CREDENTIALS")
                
                # Handle base64-encoded credentials
                try:
                    import base64
                    decoded = base64.b64decode(credentials_json)
                    credentials_dict = json.loads(decoded)
                except Exception:
                    # If not base64, try parsing as JSON directly
                    credentials_dict = json.loads(credentials_json)
                
                cred = credentials.Certificate(credentials_dict)
                firebase_admin.initialize_app(cred)
            
            else:
                raise ValueError(
                    "Firebase credentials not found. Set either:\n"
                    "  - FIREBASE_SERVICE_ACCOUNT_PATH (path to JSON file)\n"
                    "  - FIREBASE_CREDENTIALS (base64-encoded JSON or raw JSON)"
                )
        
        # Create Firestore client (synchronous for now, will wrap in async)
        _firestore_client = firestore.client()
        logger.info("Firestore client initialized successfully")
        return _firestore_client
        
    except Exception as e:
        logger.error(f"Failed to initialize Firestore: {e}")
        raise


def get_firestore():
    """
    Get the Firestore client instance, initializing if needed.
    
    Returns:
        Firestore client
    """
    if _firestore_client is None:
        return initialize_firestore()
    return _firestore_client
