import json
import os
import time
from pathlib import Path
from typing import Any

import datasets

import sycamore
from sycamore.connectors.file.file_writer import JSONEncodeWithUserDict
from sycamore.data import Element
from sycamore.evaluation import EvaluationDataPoint
from sycamore.evaluation.metrics import document_retrieval_metrics, rouge_metrics
from sycamore.evaluation.datasets import EvaluationDataSetReader
from sycamore.evaluation.pipeline import EvaluationPipeline
from sycamore.transforms.query import OpenSearchQueryExecutor

def _hf_to_qa_datapoint(datapoint: dict[str, Any]) -> dict[str, Any]:
    document = EvaluationDataPoint()

    document.question = datapoint["Query"]
    document.ground_truth_answer = datapoint["Response"]
    document.ground_truth_source_documents = [Element({
        "properties": {
            "_location": datapoint["Source"],
        }
    })]
    
    document["raw"] = datapoint
    return {"doc": document.serialize()}

INDEX = "finqabench"

if os.path.exists("/.dockerenv"):
    opensearch_host = "opensearch"
    print("Assuming we are in a sycamore jupyter container, using opensearch for opensearch host")
else:
    opensearch_host = "localhost"
    print("Assuming we are running outside of a container, using localhost for opensearch host")

OS_CLIENT_ARGS = {
    "hosts": [{"host": opensearch_host, "port": 9200}],
    "http_compress": True,
    "http_auth": ("admin", "admin"),
    "use_ssl": True,
    "verify_certs": False,
    "ssl_assert_hostname": False,
    "ssl_show_warn": False,
    "timeout": 120,
}

OS_CONFIG = {
    "size": 10,
    "neural_search_k": 200,
    "embedding_model_id": "hlAX5Y8BnK-z0ftijBv_",
    "search_pipeline": "hybrid_rag_pipeline",
    "llm": "gpt-3.5-turbo",
    "context_window": "10",
}

base_path = str(Path(__file__).parent)
output_path = "/home/admin/sycamore/examples/finqabench" + "/fqbench.json"

context = sycamore.init()
reader = EvaluationDataSetReader(context)
hf_dataset = datasets.load_dataset("lighthouzai/finqabench", split='train[:]')
input_docset = reader.huggingface(hf_dataset, doc_extractor=_hf_to_qa_datapoint)

data = {
    "experiment_name": "FinQABench trial",
    "description": "gpt-3.5-turbo",
    "created_by": "aanyapratapneni",
    "index": INDEX,
    "os_client_args": OS_CLIENT_ARGS,
    "os_config": OS_CONFIG,
    "qa_path": ["huggingface: lighthouzai/finqabench"]
}

pipeline = EvaluationPipeline(
    index=INDEX,
    os_config=OS_CONFIG,
    metrics=[],
    query_executor=OpenSearchQueryExecutor(OS_CLIENT_ARGS),
)

start = time.time()
query_level_metrics, aggregated_metrics = pipeline.execute(input_docset)
data["query_level_data"] = query_level_metrics.take_all()
data["aggregate_data"] = aggregated_metrics
data["evaluation_time"] = f'{"{:.2f}".format(time.time() - start)} seconds'
with open(output_path, "w+") as outfile:
    json.dump(data, outfile, cls=JSONEncodeWithUserDict)