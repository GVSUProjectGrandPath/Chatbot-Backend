from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Annotated,List


class NewSessionResponse(BaseModel):
    session_id : Annotated[str,Field(description="Session-Id")]

class ChatResponse(BaseModel):
    message: Annotated[[str], Field(description= "User message list")]
    session_id: Annotated[str, Field(description="Reponse from LLM ")]


app = FastAPI(title="Finlit-Backend-API")

@app.get("/health")
def health():
    return {'status':'ok'}

