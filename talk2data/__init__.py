"""Transparent compatibility shim forwarding all talk2data imports to backend."""
import sys
import backend

# Forward this module to backend in sys.modules
sys.modules["talk2data"] = backend
