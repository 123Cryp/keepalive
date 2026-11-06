#!/usr/bin/env python3
"""Builds contract_testnet_deploy.py from contract_deploy.py by replacing the
time constants only. Usage: make_testnet_deploy.py [--check]"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract_deploy.py")
TARGET = os.path.join(ROOT, "contract_testnet_deploy.py")
CHANGES = (
    ("MIN_EPOCH_SECONDS = 3600", "MIN_EPOCH_SECONDS = 300"),
    ("WARMUP_SECONDS = 900", "WARMUP_SECONDS = 60"),
    ("MIN_GAP_SECONDS = 60", "MIN_GAP_SECONDS = 30"),
    ("SETTLE_DELAY_SECONDS = 24 * 3600", "SETTLE_DELAY_SECONDS = 300"),
)


def build():
    text = open(SOURCE).read()
    for old, new in CHANGES:
        if text.count(old) != 1:
            raise SystemExit("constant not found exactly once: " + old)
        text = text.replace(old, new)
    return text


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if open(TARGET).read() == text else 1)
    open(TARGET, "w").write(text)
    print("wrote", TARGET)
