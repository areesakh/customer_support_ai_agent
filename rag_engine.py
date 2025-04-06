import numpy as np
from openai import OpenAI
import os

# Global OpenAI client that will be initialized in __init__
client = None
import faiss
from sklearn.feature_extraction.text import TfidfVectorizer
import re
from datetime import datetime
import json
import pickle
import os

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
        with open(file_path, 'r') as file:
            return file.read()
    
    def _chunk_content(self, content, chunk_size=200):
        # Split content into sections based on headers
        sections = re.split(r'\n##? ', content)
        chunks = []
        
        for section in sections:
            # Split section into smaller chunks while preserving context
            words = section.split()
            current_chunk = []
            current_length = 0
            
            for word in words:
                current_chunk.append(word)
                current_length += len(word) + 1
                
                if current_length >= chunk_size:
                    chunks.append(' '.join(current_chunk))
                    current_chunk = []
                    current_length = 0
            
            if current_chunk:
                chunks.append(' '.join(current_chunk))
        
        return chunks
    
    def _create_vectors(self):
        # This method is now just a placeholder since we create vectors in __init__
        pass
        self.index = faiss.IndexFlatL2(dimension)
        
        # Add vectors to the index
        self.index.add(self.vectors.astype('float32'))
    
    def _resources_exist(self):
        """Check if saved resources exist and are compatible with current scikit-learn version"""
        try:
            if not (os.path.exists('resources/vectorizer.pkl') and \
                    os.path.exists('resources/chunks.pkl') and \
                    os.path.exists('resources/faiss_index.bin')):
                return False
            
            # Try to load the vectorizer to check its version
            with open('resources/vectorizer.pkl', 'rb') as f:
                vectorizer = pickle.load(f)
                if not hasattr(vectorizer, '_sklearn_version') or vectorizer._sklearn_version != '1.6.1':
                    return False
            return True
        except Exception:
            return False

    def _create_cancellation_ticket(self, order_id, customer_email, reason):
        """Create a support ticket for order cancellation"""
        from app import SupportTicket, db, app
        try:
            with app.app_context():
                ticket = SupportTicket(
                    customer_email=customer_email,
                    issue=f"Order cancellation request: {reason}",
                    ticket_type='cancellation',
                    order_id=order_id
                )
                db.session.add(ticket)
                db.session.commit()
                return True
        except Exception as e:
            print(f"Error creating cancellation ticket: {str(e)}")
            return False

    def _get_order_details(self, order_id=None, customer_email=None):
        """Get order details from the database"""
        from app import Order, Employee, db, app
        try:
            with app.app_context():
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

    def get_available_allowance(self):
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
        return {
            "allowance": current_user.meal_allowance,
            "message": f"Your current meal allowance is ${current_user.meal_allowance:.2f}"
        }

    def get_available_credits(self):
        """Get the user's current available credit balance"""
        from app import current_user
        return {
            "credits": current_user.credit_balance,
            "message": f"Your current credit balance is ${current_user.credit_balance:.2f}"
        }

    def get_last_order_status(self):
        """Get the status of the user's most recent order"""
        from app import current_user
        order_details = self._get_order_details(customer_email=current_user.email)
        if order_details:
            return {
                "order_id": order_details['order_id'],
                "status": order_details['status'],
                "created_at": order_details['created_at'],
                "message": f"Your last order (#{order_details['order_id']}) is {order_details['status']}"
            }
        return {"message": "No recent orders found"}

    def get_order_status(self, order_id):
        """Get the status of a specific order"""
        order_details = self._get_order_details(order_id=order_id)
        if order_details:
            return {
                "order_id": order_details['order_id'],
                "status": order_details['status'],
                "created_at": order_details['created_at'],
                "message": f"Order #{order_details['order_id']} is {order_details['status']}"
            }
        return {"message": f"Order #{order_id} not found"}

    def escalate_to_support(self, issue):
        """Create a support ticket and escalate to customer support"""
        from app import current_user, db, SupportTicket
        try:
            ticket = SupportTicket(
                customer_email=current_user.email,
                issue=issue,
                status='open'
            )
            db.session.add(ticket)
            db.session.commit()
            
            return {
                "ticket_id": ticket.id,
                "message": "Your issue has been escalated to our customer support team. A representative will contact you shortly."
            }
        except Exception as e:
            print(f"Error creating support ticket: {str(e)}")
            return {"message": "Failed to create support ticket. Please try again later."}

    def generate_response(self, user_query, customer_email=None):
        """Generate a response using the RAG engine and OpenAI functions"""
        # Get relevant context
        context = self.get_relevant_context(user_query, customer_email=customer_email)
        
        # Add conversation history context if available
        history_context = ""
        if self.conversation_history:
            history_context = "\n\nPrevious conversation:\n" + "\n".join(
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
                model="gpt-3.5-turbo-1106",  # Using a newer model that supports tools
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
                    function_args = json.loads(tool_call.function.arguments)
                except:
                    function_args = {}
                
                # Call the appropriate function
                if function_name == "get_available_allowance":
                    function_response = self.get_available_allowance()
                elif function_name == "get_available_credits":
                    function_response = self.get_available_credits()
                elif function_name == "get_last_order_status":
                    function_response = self.get_last_order_status()
                elif function_name == "get_order_status":
                    function_response = self.get_order_status(function_args.get("order_id"))
                elif function_name == "escalate_to_support":
                    function_response = self.escalate_to_support(function_args.get("issue"))
                else:
                    function_response = {"error": "Unknown function"}
                
                # Append function call and result to conversation
                self.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": function_name,
                                "arguments": json.dumps(function_args)
                            }
                        }]
                    }
                )
                self.conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": json.dumps(function_response)
                    }
                )
                
                # Continue conversation to get final response
                continue
            
            # No function call, we have our final response
            self.conversation_history.append({"role": "user", "content": user_query})
            ai_response = message.content
            self.conversation_history.append({"role": "assistant", "content": ai_response})
            break
        
        # Maintain history size
        if len(self.conversation_history) > self.max_history * 2:  # *2 because each exchange has 2 messages
            self.conversation_history = self.conversation_history[-self.max_history * 2:]
        
        return ai_response

    def get_relevant_context(self, query, k=3, customer_email=None):
        # Check for order-related context first
        order_context = self._extract_order_context(query)
        if order_context:
            self.current_order_context = order_context
            if order_context['type'] == 'order_lookup':
                order_details = self._get_order_details(order_id=order_context['order_id'])
            else:
                order_details = self._get_order_details(customer_email=customer_email)
            
            if order_details:
                # Add order details to conversation context
                context_query = f"Order Details: {json.dumps(order_details)}\n{query}"
            else:
                context_query = f"Order not found\n{query}"
        else:
            # Regular context handling
            context_query = query
            if self.conversation_history:
                recent_context = ' '.join([f"{msg['role']}: {msg['content']}" 
                                         for msg in self.conversation_history[-2:]])
                context_query = f"{recent_context}\n{query}"
        
        # Convert query to vector using the same vectorizer
        query_vector = self.vectorizer.transform([context_query]).toarray().astype('float32')
        
        # Search for similar vectors
        distances, indices = self.index.search(query_vector, k)
        
        # Get the relevant chunks
        relevant_chunks = [self.chunks[i] for i in indices[0]]
        
        return '\n'.join(relevant_chunks)
    
    def generate_response(self, user_query, customer_email=None):
        # Get relevant context from the SOP and order history
        context = self.get_relevant_context(user_query, customer_email=customer_email)
        
        # Prepare conversation history for the prompt
        history_context = ""
        if self.conversation_history:
            history_context = "\n\nRecent conversation:\n" + "\n".join(
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
                    function_args = json.loads(tool_call.function.arguments)
                except:
                    function_args = {}
                
                # Call the appropriate function
                if function_name == "get_available_allowance":
                    function_response = self.get_available_allowance()
                elif function_name == "get_available_credits":
                    function_response = self.get_available_credits()
                elif function_name == "get_last_order_status":
                    function_response = self.get_last_order_status()
                elif function_name == "get_order_status":
                    function_response = self.get_order_status(function_args.get("order_id"))
                elif function_name == "escalate_to_support":
                    function_response = self.escalate_to_support(function_args.get("issue"))
                else:
                    function_response = {"error": "Unknown function"}
                
                # Append function call and result to conversation
                self.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "function_call": {
                            "name": function_name,
                            "arguments": json.dumps(function_args)
                        }
                    }
                )
                self.conversation_history.append(
                    {
                        "role": "function",
                        "name": function_name,
                        "content": json.dumps(function_response)
                    }
                )
                
                # Continue conversation to get final response
                continue
            
            # No function call, we have our final response
            self.conversation_history.append({"role": "user", "content": user_query})
            ai_response = message.content
            self.conversation_history.append({"role": "assistant", "content": ai_response})
            break
        
        # Maintain history size
        if len(self.conversation_history) > self.max_history * 2:  # *2 because each exchange has 2 messages
            self.conversation_history = self.conversation_history[-self.max_history * 2:]
        
        return ai_response
