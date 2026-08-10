# Mneme-rag Architecture Design v0.1


## 1. 项目定位

Mneme-rag 是一个 Python-native RAG framework。

目标：

为LLM提供外部知识记忆能力。



## 2. v0.1目标

实现：

Document

↓

Parser

↓

Chunk

↓

Embedding

↓

Vector Store

↓

Retriever

↓

Prompt Builder

↓

LLM

↓

Answer



## 3. 总体架构


                User

                 |

              FastAPI

                 |

            RAG Engine

                 |

        -----------------

        |               |

    Retriever       Prompt

        |

    Vector DB

        |

    Embedding


                 |

                LLM


