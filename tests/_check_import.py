try:
    from host_brain.app.fastapi_app import app
    print("OK: import succeeded")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
