from functools import partial
from fastprogress import progress_bar

import wandb
import pandas as pd

from datasets import load_from_disk

import evaluate
from transformers import GenerationConfig
from transformers.integrations import WandbCallback

def load_ds_from_artifact(at_address, at_type="dataset"):
    "Load a HF dataset from a W&B artifact"
    artifact = wandb.use_artifact(at_address, type=at_type)
    artifact_dir = artifact.download()
    return load_from_disk(artifact_dir)

def save_model(model, model_name, log=False):
    "Save pytorch model to disk and wandb"
    model_name = f"{wandb.run.id}_{model_name}"
    model.save_pretrained(model_name, safe_serialization=True)
        # save tokenizer for easy inference
    tokenizer = AutoTokenizer.from_pretrained(model.name_or_path)
    tokenizer.save_pretrained(model_name)
    if log:
        at = wandb.Artifact(model_name, type="model")
        at.add_dir(model_name)
        wandb.log_artifact(at)


def token_accuracy(eval_preds):
    token_accuracy = evaluate.load("accuracy")
    logits, labels = eval_preds
    predictions = np.argmax(logits, axis=-1)
    return token_accuracy.compute(predictions=predictions, references=labels)
        
        



def _generate(prompt, model, tokenizer, gen_config):
    tokenized_prompt = tokenizer(prompt, return_tensors='pt')['input_ids'].cuda()
    with torch.inference_mode():
        output = model.generate(tokenized_prompt, 
                                generation_config=gen_config)
    return tokenizer.decode(output[0][len(tokenized_prompt[0]):], skip_special_tokens=True)


class LLMSampleCB(WandbCallback):
    def __init__(self, trainer, tokenizer, test_dataset, num_samples=10, max_new_tokens=256):
        super().__init__()
        self.sample_dataset = test_dataset["train"].select(range(num_samples))
        self.gen_config = GenerationConfig.from_pretrained(trainer.model.name_or_path,
                                                           max_new_tokens=max_new_tokens)
        self.generate = partial(_generate, 
                                model=trainer.model, 
                                tokenizer=tokenizer, 
                                gen_config=self.gen_config)

    def log_generations_table(self, examples):
        records_table = wandb.Table(columns=["prompt", "generation"] + list(self.gen_config.to_dict().keys()))
        for example in progress_bar(examples, leave=False):
            prompt = example["text"]
            generation = self.generate(prompt=prompt[-1000:])
            records_table.add_data(prompt, generation, *list(self.gen_config.to_dict().values()))
        self._wandb.log({"sample_predictions":records_table})
    
    def on_evaluate(self, args, state, control,  **kwargs):
        self.log_generations_table(self.sample_dataset)