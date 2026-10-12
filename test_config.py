# test_config.py
from core import config

print("PROJECT_ROOT:", config.PROJECT_ROOT)
print("MEMORY_DIR  :", config.MEMORY_DIR)
print("LLM_BASE_URL:", config.LLM_BASE_URL)
print("LLM_MODEL   :", config.LLM_MODEL)
print("MAX_TOKENS  :", config.LLM_MAX_TOKENS)