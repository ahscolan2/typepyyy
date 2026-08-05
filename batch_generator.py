"""
Batch Dataset Generator for Project Aletheia
Generates large-scale synthetic datasets with ground-truth labels.
Outputs JSONL or Parquet formats for ML training pipelines.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib

# Import the correct function from main
from main import generate_full_output
from error_models import CognitiveErrorModel, SemanticSubstitution

class BatchGenerator:
    def __init__(self, output_dir: str = "./datasets", format: str = "jsonl"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.format = format  # 'jsonl' or 'parquet'
        self.error_model = CognitiveErrorModel()
        self.semantic_model = SemanticSubstitution()
        
    def load_corpus(self, source: str) -> List[str]:
        """
        Load text samples from a file (one sample per line) or directory of .txt files.
        """
        samples = []
        source_path = Path(source)
        
        if source_path.is_file():
            with open(source_path, 'r', encoding='utf-8') as f:
                samples = [line.strip() for line in f if line.strip()]
        elif source_path.is_dir():
            for txt_file in source_path.glob("*.txt"):
                with open(txt_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        samples.append(content)
        else:
            # Treat as raw text string if not a path
            samples = [source]
            
        return samples

    def augment_sample(self, text: str, variant_id: int) -> str:
        """
        Create a slightly modified version of the text to simulate 
        different drafting attempts or user variations.
        """
        # Simple augmentation: maybe apply semantic substitution
        if variant_id % 3 == 0:  # 33% chance
            augmented, _ = self.semantic_model.maybe_substitute(text)
            return augmented
        return text

    async def process_sample(self, sample: str, sample_id: int, profile: str, mode: str) -> Dict[str, Any]:
        """
        Process a single text sample through the Aletheia generator.
        Returns the full metadata + script record.
        """
        # Generate base data (synchronous call wrapped)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: generate_full_output(
                text=sample,
                profile=profile,
                mode=mode,
                doc_id=f"BATCH_{sample_id:05d}",
                seed=sample_id
            )
        )
        
        # Add batch-specific metadata
        result['batch_id'] = sample_id
        result['input_hash'] = hashlib.sha256(sample.encode()).hexdigest()[:16]
        result['char_count'] = len(sample)
        result['word_count'] = len(sample.split())
        
        return result

    async def generate_dataset(
        self, 
        source: str, 
        n_samples: int = 100, 
        profiles: List[str] = ['average'],
        mode: str = 'dry-run',
        max_concurrency: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Generate a full dataset.
        :param source: Path to corpus file/dir or raw text
        :param n_samples: Total number of synthetic records to generate
        :param profiles: List of profiles to cycle through
        :param mode: 'dry-run' recommended for batch
        :param max_concurrency: Number of parallel tasks
        :return: List of generated records
        """
        print(f"🚀 Starting Batch Generation: {n_samples} samples...")
        start_time = time.time()
        
        # Load base corpus
        base_samples = self.load_corpus(source)
        if not base_samples:
            raise ValueError("No samples loaded from source.")
            
        # Expand to n_samples by cycling/augmenting
        all_tasks = []
        for i in range(n_samples):
            base_idx = i % len(base_samples)
            variant_id = i // len(base_samples)
            
            text = self.augment_sample(base_samples[base_idx], variant_id)
            profile = profiles[i % len(profiles)]
            
            all_tasks.append(self.process_sample(text, i, profile, mode))
            
        # Run concurrently with semaphore
        semaphore = asyncio.Semaphore(max_concurrency)
        
        async def bounded_process(task):
            async with semaphore:
                return await task
        
        results = await asyncio.gather(*all_tasks)
        
        # Write to disk
        elapsed = time.time() - start_time
        output_file = self.output_dir / f"aletheia_batch_{int(time.time())}.{self.format}"
        
        if self.format == 'jsonl':
            with open(output_file, 'w', encoding='utf-8') as f:
                for record in results:
                    # Remove heavy macro_script if desired for size, keep stats
                    json.dump(record, f)
                    f.write('\n')
        
        print(f"✅ Dataset generated: {output_file}")
        print(f"   Samples: {len(results)}")
        print(f"   Time: {elapsed:.2f}s ({len(results)/elapsed:.1f} samples/sec)")
        
        return list(results)

if __name__ == "__main__":
    # Demo usage
    async def main():
        generator = BatchGenerator(output_dir="./demo_datasets")
        
        # Create a dummy corpus file for testing
        test_corpus = "./test_corpus.txt"
        with open(test_corpus, 'w') as f:
            f.write("The quick brown fox jumps over the lazy dog.\n")
            f.write("Academic integrity is essential for learning.\n")
            f.write("Machine learning models require diverse training data.\n")
            
        await generator.generate_dataset(
            source=test_corpus,
            n_samples=20,
            profiles=['slow', 'average', 'fast'],
            max_concurrency=5
        )
        
        os.remove(test_corpus)

    asyncio.run(main())
