"""
Main execution script - End-to-end pipeline orchestration
"""

import os
import sys
import logging
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_directories():
    """Create required directories"""
    dirs = [
        'data',
        'models',
        'logs',
        'cache'
    ]
    for d in dirs:
        Path(d).mkdir(exist_ok=True)


def run_step(step_num: int, name: str, script: str):
    """Execute a pipeline step"""
    logger.info("=" * 70)
    logger.info(f"[{step_num}/4] {name}")
    logger.info("=" * 70)
    
    try:
        result = subprocess.run(
            [sys.executable, script],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=False
        )
        
        if result.returncode == 0:
            logger.info(f"✓ {name} completed successfully\n")
            return True
        else:
            logger.error(f"✗ {name} failed with code {result.returncode}\n")
            return False
    
    except Exception as e:
        logger.error(f"✗ {name} raised exception: {e}\n")
        return False


def main():
    """Execute complete pipeline"""
    
    logger.info("\n")
    logger.info("╔" + "=" * 68 + "╗")
    logger.info("║" + " " * 15 + "IoT PREDICTIVE MAINTENANCE PIPELINE" + " " * 20 + "║")
    logger.info("╚" + "=" * 68 + "╝")
    logger.info("")
    
    # Setup
    create_directories()
    
    # Step 1: Data Generation
    success = run_step(
        1,
        "Data Generation (Synthetic IoT Telemetry)",
        "src/data_pipeline/data_generator.py"
    )
    if not success:
        logger.error("Pipeline aborted at step 1")
        return False
    
    # Step 2: Feature Engineering
    success = run_step(
        2,
        "Feature Engineering (Domain-specific indicators)",
        "src/data_pipeline/feature_engineer.py"
    )
    if not success:
        logger.error("Pipeline aborted at step 2")
        return False
    
    # Step 3: Model Training
    success = run_step(
        3,
        "Model Training (Weibull AFT + XGBoost)",
        "src/models/model_trainer.py"
    )
    if not success:
        logger.error("Pipeline aborted at step 3")
        return False
    
    # Step 4: Model Testing
    success = run_step(
        4,
        "Model Testing & Explanation Generation",
        "src/models/explainability.py"
    )
    if not success:
        logger.error("Pipeline aborted at step 4")
        return False
    
    # Success
    logger.info("\n")
    logger.info("╔" + "=" * 68 + "╗")
    logger.info("║" + " " * 20 + "✓ PIPELINE COMPLETE" + " " * 29 + "║")
    logger.info("╚" + "=" * 68 + "╝")
    
    logger.info("""
Next Steps:

1. Start the FastAPI backend:
   python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

2. Launch the Streamlit dashboard (in new terminal):
   cd src/dashboard
   streamlit run app.py

3. View the dashboard at: http://localhost:8501
   API docs at: http://localhost:8000/docs
    """)
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
