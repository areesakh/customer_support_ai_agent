import numpy as np
from openai import OpenAI
import os
import faiss
from sklearn.feature_extraction.text import TfidfVectorizer
import re
from datetime import datetime
import json
import pickle
import os

# Global OpenAI client that will be initialized in __init__
client = None

class RAGEngine:
    def __init__(self, sop_file_path, api_key=None, max_history=5):
        # Initialize OpenAI client with provided API key
        global client
        if not api_key:
            raise ValueError("API key is required for RAGEngine")
        client = OpenAI(api_key=api_key)
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_available_allowance",
                    "description": "Get the user's current available meal allowance",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_available_credits",
                    "description": "Get the user's current available credit balance",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_last_order_status",
                    "description": "Get the status of the user's most recent order",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_order_status",
                    "description": "Get the status of a specific order by its ID",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "order_id": {
                                "type": "string",
                                "description": "The ID of the order to look up"
                            }
                        },
                        "required": ["order_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "escalate_to_support",
                    "description": "Create a support ticket and escalate the issue to the customer support team",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "issue": {
                                "type": "string",
                                "description": "Description of the issue to escalate"
                            }
                        },
                        "required": ["issue"]
                    }
                }
            }
        ]
        self.sop_content = self._load_sop_file(sop_file_path)
        self.max_history = max_history
        self.conversation_history = []
        self.current_order_context = None
        
        # Try to load existing resources, if not found, create new ones
        if self._resources_exist():
            self._load_resources()
        else:
            # Process content and create new resources
            self.chunks = self._chunk_content(self.sop_content)
            self.vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
            self.vectors = self.vectorizer.fit_transform(self.chunks).toarray()
            
            # Initialize FAISS index
            dimension = self.vectors.shape[1]
            self.index = faiss.IndexFlatL2(dimension)
            self.index.add(self.vectors.astype('float32'))
            
            # Save the vectorizer and chunks mapping
            self._save_resources()
        
        # MCP patterns for order-related queries
        self.order_patterns = [
            r'order (?:number|#)?\s*[#]?(\d+)',
            r'my (?:recent )?order',
            r'order status',
            r'track (?:my )?order',
            r'where is my order'
        ]

    def _load_sop_file(self, file_path):
        """Load and preprocess the SOP file"""
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception as e:
            print(f"Error loading SOP file: {str(e)}")
            return ""

    def _chunk_content(self, content, chunk_size=200):
        """Split content into chunks of approximately equal size"""
        words = content.split()
        chunks = []
        current_chunk = []
        current_size = 0
        
        for word in words:
            current_chunk.append(word)
            current_size += 1
            
            if current_size >= chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_size = 0
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
            
        return chunks

    def _create_vectors(self):
        """Create TF-IDF vectors for the chunks"""
        try:
            self.vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
            self.vectors = self.vectorizer.fit_transform(self.chunks).toarray()
            return True
        except Exception:
            return False

    def _resources_exist(self):
        """Check if saved resources exist"""
        return os.path.exists('resources/vectorizer.pkl') and \
               os.path.exists('resources/chunks.pkl') and \
               os.path.exists('resources/faiss_index.bin')

    def _save_resources(self):
        """Save vectorizer, chunks, and FAISS index"""
        # Create a directory for resources if it doesn't exist
        os.makedirs('resources', exist_ok=True)
        
        # Save the vectorizer
        with open('resources/vectorizer.pkl', 'wb') as f:
            pickle.dump(self.vectorizer, f)
        
        # Save the chunks mapping
        with open('resources/chunks.pkl', 'wb') as f:
            pickle.dump(self.chunks, f)
            
        # Save the FAISS index
        faiss.write_index(self.index, 'resources/faiss_index.bin')

    def _load_resources(self):
        """Load saved vectorizer, chunks, and FAISS index"""
        try:
            # Load the vectorizer
            with open('resources/vectorizer.pkl', 'rb') as f:
                self.vectorizer = pickle.load(f)
            
            # Load the chunks
            with open('resources/chunks.pkl', 'rb') as f:
                self.chunks = pickle.load(f)
                
            # Load the FAISS index
            self.index = faiss.read_index('resources/faiss_index.bin')
            
            # Get vectors from the index
            self.vectors = self.index.reconstruct_n(0, self.index.ntotal)
            return True
        except Exception as e:
            print(f"Error loading resources: {str(e)}")
            return False

    def _extract_order_context(self, query):
        """Extract order context from the query using MCP patterns"""
        # Check for order number in query
        for pattern in self.order_patterns:
            match = re.search(pattern, query.lower())
            if match:
                if match.groups():  # If we captured an order number
                    return {'type': 'order_lookup', 'order_id': match.group(1)}
                return {'type': 'order_inquiry'}
        return None

    def _get_order_details(self, order_id=None, customer_email=None):
        """Get order details from the database"""
        from app import Order, Employee, db
        try:
            if order_id:
                order = Order.query.get(order_id)
            elif customer_email:
                # Get most recent order for the customer by their employee email
                employee = Employee.query.filter_by(email=customer_email).first()
                if employee:
                    order = Order.query.filter_by(employee_id=employee.id)\
                        .order_by(Order.order_time.desc()).first()
                else:
                    return None
            else:
                return None

            if order:
                return {
                    'order_id': order.id,
                    'status': order.status,
                    'total_amount': order.price * order.quantity,
                    'products': [{
                        'id': order.product_id,
                        'quantity': order.quantity,
                        'price': order.price
                    }],
                    'created_at': order.order_time.strftime('%Y-%m-%d %H:%M:%S')
                }
            return None
        except Exception as e:
            print(f"Error getting order details: {str(e)}")
            return None

    def get_available_allowance(self):
        """Get the user's current available meal allowance"""
        from app import current_user
        return current_user.meal_allowance if current_user else 0

    def get_available_credits(self):
        """Get the user's current available credit balance"""
        from app import current_user
        return current_user.credit_balance if current_user else 0

    def get_last_order_status(self):
        """Get the status of the user's most recent order"""
        from app import current_user
        order_details = self._get_order_details(customer_email=current_user.email)
        if order_details:
            return f"Your most recent order (#{order_details['order_id']}) is {order_details['status']}"
        return "No recent orders found"

    def get_order_status(self, order_id):
        """Get the status of a specific order"""
        order_details = self._get_order_details(order_id=order_id)
        if order_details:
            return f"Order #{order_id} is {order_details['status']}"
        return f"Order #{order_id} not found"

    def escalate_to_support(self, issue):
        """Create a support ticket and escalate to customer support"""
        # TODO: Implement support ticket creation
        return "Your issue has been escalated to our support team. They will contact you shortly."

    def get_relevant_context(self, query, k=3, customer_email=None):
        """Get relevant context from the SOP based on the query"""
        # Check for order-related context first
        order_context = self._extract_order_context(query)
        if order_context:
            if order_context.get('order_id'):
                order_details = self._get_order_details(order_id=order_context['order_id'])
            else:
                order_details = self._get_order_details(customer_email=customer_email)
            
            if order_details:
                return f"Order Details:\n" + json.dumps(order_details, indent=2)
        
        # If no order context or no order found, search SOP
        query_vector = self.vectorizer.transform([query]).toarray().astype('float32')
        distances, indices = self.index.search(query_vector, k)
        
        context = []
        for idx in indices[0]:
            if idx < len(self.chunks):
                context.append(self.chunks[idx])
        
        return "\n".join(context)

    def generate_response(self, user_query, customer_email=None):
        """Generate a response using the RAG engine and OpenAI functions"""
        # Get relevant context from SOP
        context = self.get_relevant_context(user_query, customer_email=customer_email)
        
        # Add query to conversation history
        self.conversation_history.append({"role": "user", "content": user_query})
        
        # Get the last 2 messages for context (if they exist)
        history_context = "\nConversation History:\n" + "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in self.conversation_history[-2:]]
        )
        
        # Create prompt with context and conversation history
        prompt = f"""As a customer support AI, use the following context from our support SOP and the conversation history to answer the user's question.
        If the context doesn't contain relevant information, provide a general helpful response.
        
        Context from SOP:
        {context}
        {history_context}
        
        User Question: {user_query}
        
        Please provide a helpful, professional response that maintains conversation continuity:"""
        
        # Generate response using OpenAI with function calling
        while True:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful customer support agent with access to the conversation history."}
                ] + self.conversation_history + [
                    {"role": "user", "content": prompt}
                ],
                tools=self.tools,
                tool_choice="auto"
            )
            
            message = response.choices[0].message
            
            # Check if OpenAI wants to call a function
            if message.tool_calls:
                tool_call = message.tool_calls[0]  # We only handle one tool call at a time
                function_name = tool_call.function.name
                try:
                    # Get the function from our class
                    function = getattr(self, function_name)
                    
                    # Parse the arguments
                    arguments = json.loads(tool_call.function.arguments)
                    
                    # Call the function with the provided arguments
                    function_response = function(**arguments)
                    
                    # Add function response to conversation
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call]
                    })
                    self.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(function_response)
                    })
                    
                except Exception as e:
                    print(f"Error calling function {function_name}: {str(e)}")
                    return "I apologize, but I encountered an error while processing your request. Please try again or contact support if the issue persists."
            else:
                # Add response to conversation history
                self.conversation_history.append({
                    "role": "assistant",
                    "content": message.content
                })
                return message.content
