name: SCOUT 🚨

on:
  workflow_dispatch:
  schedule:
    - cron: "*/10 * * * *"

concurrency:
  group: scout
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  scout:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout SCOUT
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run SCOUT
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python scout.py

      - name: Save SCOUT state
        if: always()
        run: |
          if [ ! -f scout_state.json ]; then
            echo "No state file created."
            exit 0
          fi

          if git diff --quiet -- scout_state.json; then
            echo "No state changes."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add scout_state.json
          git commit -m "Update SCOUT observed ATL state"
          git push
