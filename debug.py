# debug.py
try:
    import main
except Exception as e:
    print(f"ОШИБКА: {e}")
    import traceback
    traceback.print_exc()
    input("Нажмите Enter для выхода...")