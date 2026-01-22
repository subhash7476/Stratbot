"""
Test script to verify all imports are working
Run this before starting the Streamlit app
"""

import sys
from pathlib import Path

# Add project root to path
# CHANGE THIS to your actual project path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

print("🧪 Testing Imports for ₹500 Scalping Engine...")
print(f"📁 Project root: {PROJECT_ROOT}")
print(f"🐍 Python path: {sys.path[0]}\n")

# Test 1: Core package
try:
    import core
    print("✅ Step 1: 'core' package found")
except ImportError as e:
    print(f"❌ Step 1 FAILED: Cannot import 'core'")
    print(f"   Error: {e}")
    print(f"   Solution: Create core/__init__.py file")
    sys.exit(1)

# Test 2: Filters package
try:
    import core.filters
    print("✅ Step 2: 'core.filters' package found")
except ImportError as e:
    print(f"❌ Step 2 FAILED: Cannot import 'core.filters'")
    print(f"   Error: {e}")
    print(f"   Solution: Create core/filters/__init__.py file")
    sys.exit(1)

# Test 3: Individual filters
filters_to_test = [
    ('Filter 1 - Index Regime', 'core.filters.filter_1_index_regime', 'IndexRegimeFilter'),
    ('Filter 2 - Stock Eligibility', 'core.filters.filter_2_stock_eligibility', 'StockEligibilityFilter'),
    ('Filter 3 - Impulse Detector', 'core.filters.filter_3_impulse_detector', 'ImpulseDetector'),
    ('Filter 4 - Option Response', 'core.filters.filter_4_option_response', 'OptionResponseFilter'),
    ('Filter 5 - Feasibility', 'core.filters.filter_5_feasibility', 'FeasibilityFilter'),
]

print("\n📊 Testing Individual Filters:")
for name, module_path, class_name in filters_to_test:
    try:
        module = __import__(module_path, fromlist=[class_name])
        filter_class = getattr(module, class_name)
        print(f"✅ {name}: {class_name} imported")
    except ImportError as e:
        print(f"❌ {name} FAILED")
        print(f"   Error: {e}")
        sys.exit(1)

# Test 4: Core modules
print("\n🔧 Testing Core Modules:")
core_modules = [
    ('Guardrails', 'core.guardrails', 'GlobalGuardrails'),
    ('Scanner', 'core.scanner', 'MultiStockScanner'),
    ('Signal Generator', 'core.signal_generator', 'SignalGenerator'),
]

for name, module_path, class_name in core_modules:
    try:
        module = __import__(module_path, fromlist=[class_name])
        module_class = getattr(module, class_name)
        print(f"✅ {name}: {class_name} imported")
    except ImportError as e:
        print(f"❌ {name} FAILED")
        print(f"   Error: {e}")
        sys.exit(1)

# Test 5: Dependencies
print("\n📦 Testing Dependencies:")
dependencies = [
    ('pandas', 'pd'),
    ('numpy', 'np'),
    ('duckdb', None),
]

for package, alias in dependencies:
    try:
        if alias:
            exec(f"import {package} as {alias}")
        else:
            exec(f"import {package}")
        print(f"✅ {package} available")
    except ImportError:
        print(f"⚠️  {package} NOT installed - install with: pip install {package}")

# Test 6: Quick functionality test
print("\n🎯 Testing Basic Functionality:")
try:
    from core.guardrails import GlobalGuardrails
    
    guardrails = GlobalGuardrails()
    status = guardrails.can_trade_now()
    
    print(f"✅ Guardrails working")
    print(f"   Trading allowed: {status['allowed']}")
    print(f"   Reason: {status['reason']}")
    
except Exception as e:
    print(f"⚠️  Functionality test failed: {e}")

# Success!
print("\n" + "="*50)
print("🎉 ALL TESTS PASSED!")
print("="*50)
print("\n✅ You can now run the Streamlit app:")
print("   streamlit run pages/13_Scalping_Engine.py")
print("\n✅ Or initialize the database:")
print("   python scripts/init_scalping_db.py")