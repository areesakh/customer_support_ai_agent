import numpy as np
import openai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re

import re
from datetime import datetime

class RAGEngine:
    def __init__(self, sop_file_path, max_history=5):
        self.sop_content = self._load_sop_file(sop_file_path)
        self.chunks = self._chunk_content(self.sop_content)
        self.vectorizer = TfidfVectorizer()
        self.vectors = self._create_vectors()
        self.index = self._create_index()
        self.max_history = max_history
        self.conversation_history = []
        self.current_order_context = None
        
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
        return self.vectorizer.fit_transform(self.chunks).toarray()
    
    def _create_index(self):
        # No need for index with cosine similarity
        return None
    
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
        from app import Order, db
        try:
            if order_id:
                order = Order.query.get(order_id)
            elif customer_email:
                # Get most recent order for the customer
                order = Order.query.filter_by(customer_email=customer_email)\
                    .order_by(Order.created_at.desc()).first()
            else:
                return None

            if order:
                products = json.loads(order.products)
                return {
                    'order_id': order.id,
                    'status': order.status,
                    'total_amount': order.total_amount,
                    'products': products,
                    'created_at': order.created_at.strftime('%Y-%m-%d %H:%M:%S')
                }
            return None
        except Exception as e:
            print(f"Error getting order details: {str(e)}")
            return None

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
        
        # Convert context-enhanced query to vector
        query_vector = self.vectorizer.transform([context_query])
        
        # Calculate similarity with all chunks
        similarities = cosine_similarity(query_vector, self.vectorizer.transform(self.chunks))[0]
        
        # Get top k chunks
        top_k_indices = np.argsort(similarities)[-k:]
        relevant_chunks = [self.chunks[i] for i in top_k_indices]
        
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
        
        # Generate response using OpenAI
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful customer support agent with access to the conversation history."}
            ] + self.conversation_history + [
                {"role": "user", "content": prompt}
            ]
        )
        
        # Update conversation history
        self.conversation_history.append({"role": "user", "content": user_query})
        ai_response = response.choices[0].message['content']
        self.conversation_history.append({"role": "assistant", "content": ai_response})
        
        # Maintain history size
        if len(self.conversation_history) > self.max_history * 2:  # *2 because each exchange has 2 messages
            self.conversation_history = self.conversation_history[-self.max_history * 2:]
        
        return ai_response
