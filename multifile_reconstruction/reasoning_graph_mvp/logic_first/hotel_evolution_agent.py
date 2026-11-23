"""Run the official three-stage hotel capability-evolution experiment."""

from .hotel_graph import ARTIFACTS
from .hotel_revenue_milestones_agent import main as run_three_stage


def main():
    from .hotel_evolution import prepare_stage

    run_three_stage(default_source=ARTIFACTS / 'hotel_evolution', prepare_stage=prepare_stage)


if __name__ == '__main__':
    main()
