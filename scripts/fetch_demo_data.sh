#!/usr/bin/env bash
# Download the FASTQs that nf-core/demo's test profile uses into data/demo, for the M2 done-check.
# Same files as https://raw.githubusercontent.com/nf-core/test-datasets/viralrecon/samplesheet/samplesheet_test_illumina_amplicon.csv
set -euo pipefail

base="https://raw.githubusercontent.com/nf-core/test-datasets/viralrecon/illumina/amplicon"
dest="$(cd "$(dirname "$0")/.." && pwd)/data/demo"
mkdir -p "$dest"

for file in sample1_R1.fastq.gz sample1_R2.fastq.gz sample2_R1.fastq.gz sample2_R2.fastq.gz; do
    curl -fsSL -o "$dest/$file" "$base/$file"
    echo "ok   $dest/$file"
done
