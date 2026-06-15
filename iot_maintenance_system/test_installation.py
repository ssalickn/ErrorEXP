"""
Validation script to test IoT Maintenance System installation and basic functionality
Run this after setup to verify everything works
"""

import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_imports():
    """Test critical library imports"""
    logger.info("Testing imports...")
    
    required_libs = [
        ('numpy', 'NumPy'),
        ('pandas', 'Pandas'),
        ('sklearn', 'Scikit-learn'),
        ('lifelines', 'Lifelines'),
        ('xgboost', 'XGBoost'),
        ('shap', 'SHAP'),
        ('fastapi', 'FastAPI'),
        ('streamlit', 'Streamlit'),
    ]
    
    failed = []
    for module, name in required_libs:
        try:
            __import__(module)
            logger.info(f"  ✓ {name}")
        except ImportError:
            logger.error(f"  ✗ {name} not found")
            failed.append(name)
    
    return len(failed) == 0, failed


def test_directory_structure():
    """Test project directory structure"""
    logger.info("\nTesting directory structure...")
    
    required_dirs = [
        'config',
        'src',
        'src/data_pipeline',
        'src/models',
        'src/api',
        'src/dashboard',
    ]
    
    failed = []
    for d in required_dirs:
        if os.path.isdir(d):
            logger.info(f"  ✓ {d}/")
        else:
            logger.error(f"  ✗ {d}/ not found")
            failed.append(d)
    
    return len(failed) == 0, failed


def test_file_structure():
    """Test required files"""
    logger.info("\nTesting file structure...")
    
    required_files = [
        'config/config.yaml',
        'src/data_pipeline/data_generator.py',
        'src/data_pipeline/feature_engineer.py',
        'src/models/model_trainer.py',
        'src/models/explainability.py',
        'src/api/main.py',
        'src/dashboard/app.py',
        'src/utils.py',
        'requirements.txt',
        'README.md',
    ]
    
    failed = []
    for f in required_files:
        if os.path.isfile(f):
            logger.info(f"  ✓ {f}")
        else:
            logger.error(f"  ✗ {f} not found")
            failed.append(f)
    
    return len(failed) == 0, failed


def test_config():
    """Test config file"""
    logger.info("\nTesting configuration...")
    
    try:
        import yaml
        with open('config/config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        
        required_keys = ['database', 'devices', 'models', 'alerting']
        for key in required_keys:
            if key in config:
                logger.info(f"  ✓ {key} configured")
            else:
                logger.error(f"  ✗ {key} missing")
                return False, [key]
        
        return True, []
    except Exception as e:
        logger.error(f"  ✗ Config error: {e}")
        return False, [str(e)]


def test_models_exist():
    """Test if models directory exists"""
    logger.info("\nTesting models directory...")
    
    if os.path.isdir('models'):
        files = os.listdir('models')
        if files:
            logger.info(f"  ✓ Models directory exists ({len(files)} files)")
            return True, []
        else:
            logger.info("  ⓘ Models directory is empty (run pipeline first)")
            return True, []
    else:
        logger.info("  ⓘ Models directory not created yet (run pipeline first)")
        return True, []


def test_data_exists():
    """Test if data exists"""
    logger.info("\nTesting data directory...")
    
    if os.path.isdir('data'):
        files = os.listdir('data')
        if files:
            logger.info(f"  ✓ Data directory exists ({len(files)} files)")
            return True, []
        else:
            logger.info("  ⓘ Data directory is empty (run pipeline first)")
            return True, []
    else:
        logger.info("  ⓘ Data directory not created yet (run pipeline first)")
        return True, []


def main():
    """Run all tests"""
    
    logger.info("\n" + "="*60)
    logger.info("IoT PREDICTIVE MAINTENANCE SYSTEM - VALIDATION")
    logger.info("="*60)
    
    results = []
    
    # Run tests
    results.append(("Imports", *test_imports()))
    results.append(("Directory Structure", *test_directory_structure()))
    results.append(("File Structure", *test_file_structure()))
    results.append(("Configuration", *test_config()))
    results.append(("Models", *test_models_exist()))
    results.append(("Data", *test_data_exists()))
    
    # Summary
    logger.info("\n" + "="*60)
    logger.info("VALIDATION SUMMARY")
    logger.info("="*60)
    
    all_passed = all(passed for _, passed, _ in results)
    
    for name, passed, failed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{status}: {name}")
        if failed:
            for item in failed:
                logger.error(f"       - {item}")
    
    logger.info("="*60)
    
    if all_passed:
        logger.info("\n✓ ALL CHECKS PASSED")
        logger.info("\nNext steps:")
        logger.info("1. Run: python run_pipeline.py")
        logger.info("2. Then: python -m uvicorn src.api.main:app --reload")
        logger.info("3. And:  cd src/dashboard && streamlit run app.py")
        logger.info("\nAccess dashboard at: http://localhost:8501")
        return 0
    else:
        logger.error("\n✗ SOME CHECKS FAILED")
        logger.error("\nPlease fix the issues above and try again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
