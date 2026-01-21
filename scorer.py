import os
import logging
import shutil
from typing import Dict
from pathlib import Path

from clemcore.clemgame import GameScorer, metrics
from src.utils.s3_manager import S3Manager

logger = logging.getLogger(__name__)


class ColabGameScorer(GameScorer):
    def __init__(self, name: str, experiment: Dict, game_instance: Dict):
        super().__init__(name, experiment, game_instance)

        # Initialize S3 manager for downloading images
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION")
        s3_bucket = os.getenv("S3_BUCKET_NAME")

        self.s3_manager = S3Manager(aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key, region_name=aws_region)
        self.s3_bucket = s3_bucket
        self.image_manager_s3_prefix = None

    def _on_store_scores(self, file_path: str):
        interactions_dir = str(Path(file_path).parent)

        if self.image_manager_s3_prefix:
            data_dir = os.path.join(interactions_dir, "images")
            if os.path.exists(data_dir):
                shutil.rmtree(data_dir)
            os.makedirs(data_dir)
            try:
                downloaded_files = self.s3_manager.download_directory(bucket_name=self.s3_bucket, s3_prefix=self.image_manager_s3_prefix, local_dir=data_dir)
                logger.info(f"Successfully downloaded {len(downloaded_files)} files from S3")
            except Exception as e:
                logger.error(f"Failed to download S3 directory: {e}")
        else:
            logger.info("No image_manager_s3_prefix available, skipping S3 download")

    def score_rounds(self, interactions: Dict) -> None:
        """Override base score_rounds to handle potentially empty rounds (request_count=0) safely."""
        for round_idx, round_events in enumerate(interactions["turns"]):
            # compute standard framework metrics for the round
            round_request_count = interactions[metrics.METRIC_REQUEST_COUNT][round_idx]
            self.log_round_score(round_idx, metrics.METRIC_REQUEST_COUNT, round_request_count)

            round_violated_request_count = interactions[metrics.METRIC_REQUEST_COUNT_VIOLATED][round_idx]
            self.log_round_score(round_idx, metrics.METRIC_REQUEST_COUNT_VIOLATED, round_violated_request_count)

            round_parsed_request_count = interactions[metrics.METRIC_REQUEST_COUNT_PARSED][round_idx]
            self.log_round_score(round_idx, metrics.METRIC_REQUEST_COUNT_PARSED, round_parsed_request_count)

            # Safety check for division by zero
            if round_request_count > 0:
                round_request_success_ratio = round_parsed_request_count / round_request_count
            else:
                round_request_success_ratio = 0.0

            self.log_round_score(round_idx, metrics.METRIC_REQUEST_SUCCESS_RATIO, round_request_success_ratio)

            # compute game specific round metrics
            self.compute_round_score(round_idx, round_events)

    def compute_episode_scores(self, interactions: Dict):
        """Compute game-specific episode scores. Base class handles standard metrics."""
        # Step 1: Store the image manager s3 prefix for later use (_on_store_score)
        self.image_manager_s3_prefix = interactions.get("image_manager_s3_prefix", None)

        # Step 2: Extract key episode-level data
        success = interactions.get("success", False)
        player_stats = interactions.get("player_stats", {})

        # Step 3: Calculate and log the final BENCH_SCORE
        # BENCH_SCORE equals success * 100
        bench_score = success * 100
        self.log_episode_score(metrics.BENCH_SCORE, bench_score)

        # Step 4: Log per-player scores for detailed episode-level diagnostics
        for player_id, stats in player_stats.items():
            p_requests = stats.get("requests", 0)
            p_parsed = stats.get("parsed", 0)
            p_violated = stats.get("violated", 0)

            self.log_episode_score(f"{player_id}_{metrics.METRIC_REQUEST_COUNT}", p_requests)
            self.log_episode_score(f"{player_id}_{metrics.METRIC_REQUEST_COUNT_PARSED}", p_parsed)
            self.log_episode_score(f"{player_id}_{metrics.METRIC_REQUEST_COUNT_VIOLATED}", p_violated)
            self.log_episode_score(f"{player_id}_Violated_Streak", stats.get("violated_streak", 0))

    def compute_round_score(self, round_idx: int, round_events) -> None:
        """Compute game-specific round scores. Base class handles standard metrics."""
        # Game-specific per-round scoring can be added here
        # The base class already logs standard request counts per round
        pass
