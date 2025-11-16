from vertexai import rag
from vertexai.generative_models import GenerativeModel, Tool
import vertexai

# Cấu hình dự án
PROJECT_ID = "reflecting-surf-477600-p4"  # Replace with your project ID
LOCATION = "asia-southeast1"  # Try a different region if needed
display_name = "my-test-corpus"
paths = ["https://drive.google.com/file/d/1UrWQ0CySgpiD4Yan-EBt0rKUyTpq8Oji/view?usp=sharing"]  # Add valid paths

# Khởi tạo Vertex AI
vertexai.init(project=PROJECT_ID, location=LOCATION)
print(f"Initialized Vertex AI in project {PROJECT_ID} at location {LOCATION}")

# Tạo RagCorpus
embedding_model_config = rag.RagEmbeddingModelConfig(
    vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
        publisher_model="publishers/google/models/text-embedding-004"
    )
)

rag_corpus = rag.create_corpus(
    display_name=display_name,
    backend_config=rag.RagVectorDbConfig(
        rag_embedding_model_config=embedding_model_config
    ),
)
print(f"Created corpus: {rag_corpus.name}")

# Import files
if paths:
    rag.import_files(rag_corpus.name, paths, max_embedding_requests_per_min=1000)
    print("Files imported successfully.")
else:
    print("Warning: No paths provided.")

# Cấu hình retrieval
rag_retrieval_config = rag.RagRetrievalConfig(
    top_k=5
    # ,filter=rag.Filter(vector_distance_threshold=0.5),
)

# Retrieval query
query_text = "Đối tượng nghiên cứu môn học Lịch sử Đảng Cộng sản Việt Nam?"
response = rag.retrieval_query(
    rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
    text=query_text,
    rag_retrieval_config=rag_retrieval_config,
)
print("\nRetrieval Response:")
print(response)

# Tạo RAG retrieval tool
rag_retrieval_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(
        source=rag.VertexRagStore(
            rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
            rag_retrieval_config=rag_retrieval_config,
        ),
    )
)

# Tạo model Gemini
rag_model = GenerativeModel(
    model_name="gemini-2.5-flash",  # Use correct model name
    tools=[rag_retrieval_tool]
)

# Generate response
generation_response = rag_model.generate_content(query_text)
print("\nGenerated Response with RAG:")
print(generation_response.text)