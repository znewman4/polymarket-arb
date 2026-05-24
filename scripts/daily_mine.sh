#!/bin/bash
cd ~/projects/polymarket-arb
echo "$(date): Starting daily relationship mining..."

# Fetch latest markets
python -m polymarket_arb.cli gamma fetch-markets --all

# Generate new relationship candidates
python -m polymarket_arb.cli relationships generate

# Sync to S3
aws s3 sync data/normalised/relationship_candidates/ \
  s3://polymarket-arb-data-znewman/relationship_candidates/ \
  --region eu-west-1

echo "$(date): Done. Syncing to EC2..."

# Pull to EC2 via SSM
aws ssm send-command \
  --instance-ids i-0a6672c60a510b3bf \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["aws s3 sync s3://polymarket-arb-data-znewman/relationship_candidates/ /home/ssm-user/polymarket-arb/data/normalised/relationship_candidates/ --region eu-west-1"]' \
  --region eu-west-1

echo "$(date): Complete."
