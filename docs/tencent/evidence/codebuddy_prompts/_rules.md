Working rules for this session (follow strictly):
- Work only inside this repository (the current directory). Do not read or write anything outside it.
- Do NOT run any git command.
- Create or modify ONLY the files this task names. Do not edit any other file.
- Never open, print, copy or log databridge/.env.ib.local or any key, token or secret.
- All data is synthetic ("Company A"). Never use a real company's name.
- Python: use databridge/.venv/Scripts/python.exe. To run Internal Brain tests, from the databridge/ folder set
  PYTHONPATH to "apps/internal_brain;apps/mock_platforms;packages/core;packages/harness;packages/adapter;packages/retrieval;apps/internal_brain/tests"
  (Windows, ';' separator) and run: .venv/Scripts/python.exe -m pytest apps/internal_brain/tests/<file> -q -p no:cacheprovider
- When done, reply with a short summary: files created, tests run and their result.

TASK:
