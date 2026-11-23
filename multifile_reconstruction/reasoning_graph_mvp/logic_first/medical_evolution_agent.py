"""Run the validated medical milestones through the native Office harness."""
from .hotel_revenue_milestones_agent import main as run_three_stage
from .medical_evolution import DEFAULT, prepare_stage


if __name__ == '__main__':
    run_three_stage(default_source=DEFAULT, prepare_stage=prepare_stage)
