from typing import Union
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError
import httpx

app = FastAPI()
templates = Jinja2Templates(directory="templates")
http_client = httpx.AsyncClient()

@app.get("/", response_class=HTMLResponse)
def read_root(request:Request):
    context= {"request":request, "name":"test name"}
    return templates.TemplateResponse("index.html", context)


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None):
    return {"item_id": item_id, "q": q}

def readItems(query, limit:int = 10, skip:int=0):
    cur=None
    data = []
    try:
        dbSession = connectDb()
        cur = dbSession.cursor(cursor_factory=RealDictCursor)

        cur.execute(query, (limit, skip))
        data = cur.fetchall()
        cur.close()
    except (Exception, psycopg2.Error) as error:
        print(f"Database error: {error}")
        # In a real app, you would log this and raise an HTTPException
        raise error  # Re-raise the error so FastAPI knows something went wrong
    finally:
        if cur:
            # 🐛 FIX: Ensure the connection is always closed
            cur.close()

    return data

def searchItems(query, search, limit:int = 10, skip:int=0):
    cur=None
    data = []
    dbSession=None
    try:
        dbSession = connectDb()
        cur = dbSession.cursor(cursor_factory=RealDictCursor)
        sql_query, params = build_search_query(query, limit, skip, search)

        # 2. Execute the dynamic query
        cur.execute(sql_query, params)
        data = cur.fetchall()
        cur.close()
    except (Exception, psycopg2.Error) as error:
        print(f"Database error: {error}")
        raise error
    finally:
        if dbSession:
            dbSession.close()

    return data


def build_search_query(
        base_query_select: str,
        limit: int,
        skip: int,
        search: Union[str, None]
) -> tuple[str, tuple]:
    # The base query to select columns from the 'books' table
    # Example: base_query_select = "SELECT id, authorid, title FROM books"

    conditions = []
    params = []

    if search:
        # 1. Add the WHERE clause for search.
        # We search in both title and authorid (or an author name field if you have one)
        # conditions.append("(title ILIKE %s OR CAST(authorid AS TEXT) ILIKE %s)")
        conditions.append("(title ILIKE %s)")

        # 2. Create the search string with wildcards for partial matches
        search_term = f"%{search}%"

        # 3. Add the search term twice to the parameters list (for title and authorid)
        params.extend([search_term])

    # --- Assemble the Final Query ---

    sql_parts = [base_query_select]

    if conditions:
        sql_parts.append("WHERE " + " AND ".join(conditions))

    sql_parts.append("LIMIT %s OFFSET %s")

    # Add limit and skip to the parameters
    params.extend([limit, skip])

    final_query = " ".join(sql_parts)

    return final_query, tuple(params)

def connectDb():
    connection = psycopg2.connect(database="postgres", user="postgres", password="root", host="localhost", port=5432)
    return connection

class Book(BaseModel):
    id:int
    author_id:int
    title:str

@app.get("/books/", response_model=list[Book])
async def readBooks(skip = 0, limit = 10):
    query = "SELECT id, author_id, title FROM books LIMIT %s OFFSET %s"
    books = readItems(query, limit, skip)
    return books

@app.get("/books/search", response_model=list[Book])
async def searchBooks(search = "", skip = 0, limit = 10):
    query = "SELECT id, author_id, title FROM books"
    books = searchItems(query, search, limit, skip)
    return books

from typing import List, Union, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
class GoogleBook(BaseModel):
    title: str = Field('No Title Available')
    authors: List[str] = Field(default_factory=list)
    publisher: Union[str, None] = None
    publishedDate: Union[str, None] = None
    # We can use Field to set a default value for complex nested data
    infoLink: str = Field(default=None, alias='infoLink')

    # Configuration to handle the complex dictionary structure from the API
    class Config:
        populate_by_name = True # Allows Pydantic to use the alias 'infoLink'

async def searchBooksAPI(search, limit, skip):
    BASE_URL = "https://www.googleapis.com/books/v1/volumes"
    params = {
        "q": search,  # The search term (e.g., "fastapi python")
        "maxResults": limit,  # Pagination limit (max 40)
        "startIndex": skip,  # Pagination offset
        # "key": "YOUR_API_KEY", # Optional: Use your API key for higher limits
    }

    async with httpx.AsyncClient() as client:
        try:
            # 1. Make the GET request
            response = await client.get(BASE_URL, params=params, timeout=5)
            response.raise_for_status()  # Raises an exception for 4xx/5xx status codes

            data = response.json()

            # 2. Check if the 'items' key exists and if there are results
            if not data.get('items'):
                return []

            books_list = []

            # 3. Iterate and map/clean the data
            for item in data['items']:
                volume_info = item.get('volumeInfo', {})

                # Manual data cleaning/default assignment to ensure consistency
                book_data = {
                    'title': volume_info.get('title'),
                    'authors': volume_info.get('authors', []),
                    'publisher': volume_info.get('publisher'),
                    'publishedDate': volume_info.get('publishedDate'),
                    'infoLink': volume_info.get('infoLink')
                }

                # 4. Validate and create the Pydantic model
                books_list.append(GoogleBook.model_validate(book_data))

            return books_list

        except httpx.HTTPStatusError as e:
            # Handle bad status codes (e.g., 404, 503)
            raise HTTPException(status_code=e.response.status_code, detail=f"Google Books API error: {e.response.text}")
        except httpx.RequestError:
            # Handle connection errors, DNS failure, etc.
            raise HTTPException(status_code=503, detail="Could not connect to the Google Books API service.")
        except ValidationError as e:
            # 💡 Allow the Pydantic error to show up as an informative 422
            raise HTTPException(status_code=422, detail=f"Data validation failed: {e.errors()}")
        except Exception as e:
            # Only catch truly unexpected errors here
            # 🐛 Print the error for debugging, then raise 500
            print(f"UNHANDLED ERROR: {e}")
            raise HTTPException(status_code=500, detail=f"An unexpected internal error occurred. {e}")

@app.get("/books/search2", response_model=list[GoogleBook])
async def searchBooks(search = "", skip = 0, limit = 10):
    books = await searchBooksAPI(search, limit, skip)
    return books