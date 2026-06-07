try:
    import PIL
    import PIL.Image
    print("PIL imported successfully")
    print(f"PIL version: {PIL.__version__}")
    print(f"PIL file: {PIL.__file__}")
except ImportError as e:
    print(f"Error importing PIL: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
