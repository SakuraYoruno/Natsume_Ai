"""Test script for FastAPI app import and basic functionality."""

import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from host_brain.app.fastapi_app import app
    print("[OK] FastAPI app imported successfully")

    # Test basic FastAPI app properties
    assert hasattr(app, 'title'), "App should have title attribute"
    assert app.title == "Nastume AI Host Brain", f"App title should be 'Nastume AI Host Brain', got '{app.title}'"
    print("[OK] FastAPI app properties are correct")

    # Test routes
    routes = [route.path for route in app.routes]
    expected_routes = ["/health", "/ready", "/observability/trace", "/startup", "/"]

    for route in expected_routes:
        assert route in routes, f"Route {route} should be registered"
    print("[OK] All expected routes are registered")

    print("\n[SUCCESS] FastAPI app test passed! The HTTP service framework is ready.")

except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
