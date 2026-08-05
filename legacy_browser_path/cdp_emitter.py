"""
Project Aletheia - CDP Emitter
Chrome DevTools Protocol emitter for Playwright integration.
Ports GitLitAF/Auto-Type iframe handling to Python Playwright.
"""

import asyncio
import json
from typing import List, Optional, Dict, Any
from dataclasses import asdict

# Try to import playwright, but allow dry-run without it
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

class CDPEmitter:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.cdp_session = None
        
        # Shift state tracking (from Auto-Type logic)
        self.shift_pressed = False
        self.current_shift_state = False
        
    async def start(self):
        """Initialize browser and CDP session."""
        if self.dry_run:
            return
        
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright not installed. Run: pip install playwright")
        
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        
        # Create CDP session
        self.cdp_session = await self.page.context.new_cdp_session(self.page)
    
    async def stop(self):
        """Close browser and cleanup."""
        if self.browser:
            await self.browser.close()
    
    def _normalize_char(self, char: str) -> str:
        """Normalize character for key event."""
        return char
    
    def _get_key_code(self, char: str) -> str:
        """Get key code for character."""
        special_keys = {
            ' ': 'Space',
            '\n': 'Enter',
            '\t': 'Tab',
        }
        if char in special_keys:
            return special_keys[char]
        
        if len(char) == 1:
            if char.isupper():
                return char.upper()
            return char.lower()
        
        return char
    
    def _needs_shift(self, char: str) -> bool:
        """Check if character requires Shift key."""
        shift_chars = set("!@#$%^&*()_+{}|:\"<>?~ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        return char in shift_chars or char.isupper()
    
    async def _set_shift(self, needs_shift: bool):
        """Manage Shift key state."""
        if needs_shift != self.current_shift_state:
            if needs_shift:
                await self._send_key_event('Shift', 'keyDown')
                self.shift_pressed = True
            else:
                await self._send_key_event('Shift', 'keyUp')
                self.shift_pressed = False
            self.current_shift_state = needs_shift
    
    async def _send_key_event(self, key: str, event_type: str, char: str = None, 
                              timestamp: float = None, modifiers: int = 0):
        """Send a single key event via CDP."""
        if self.dry_run:
            return
        
        if not self.cdp_session:
            return
        
        # Map event type
        native_type = {
            'keyDown': 'keyDown',
            'keyUp': 'keyUp',
            'char': 'char'
        }.get(event_type, event_type)
        
        # Build key event payload
        payload = {
            "type": native_type,
            "key": key,
            "code": f"Key{key.upper()}" if len(key) == 1 and key.isalpha() else key,
            "text": char if char else key,
            "modifiers": modifiers,
            "timestamp": timestamp
        }
        
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}
        
        await self.cdp_session.send('Input.dispatchKeyEvent', payload)
    
    async def type_character(self, char: str, timestamp_ms: float = 0):
        """Type a single character with proper Shift handling."""
        needs_shift = self._needs_shift(char)
        
        # Handle Shift
        await self._set_shift(needs_shift)
        
        key = self._get_key_code(char)
        
        # KeyDown
        await self._send_key_event(key, 'keyDown', char=char, timestamp=timestamp_ms)
        
        # Char event (for actual input)
        await self._send_key_event(key, 'char', char=char, timestamp=timestamp_ms + 1)
        
        # KeyUp
        await self._send_key_event(key, 'keyUp', char=char, timestamp=timestamp_ms + 2)
        
        # Reset shift if we pressed it just for this char
        if needs_shift and not char.isupper():
            await self._set_shift(False)
    
    async def delete_characters(self, count: int, base_timestamp: float):
        """Delete specified number of characters."""
        for i in range(count):
            ts = base_timestamp + (i * 100)
            await self._send_key_event('Backspace', 'keyDown', timestamp=ts)
            await self._send_key_event('Backspace', 'char', timestamp=ts + 1)
            await self._send_key_event('Backspace', 'keyUp', timestamp=ts + 2)
    
    async def execute_script(self, script: List[Dict], output_file: str = None):
        """
        Execute a macro script.
        If dry_run, just writes the JSON representation.
        """
        execution_log = []
        current_time = 0.0
        
        for event in script:
            op = event.get('op')
            data = event.get('data')
            
            if op == 'TYPE':
                char = data
                execution_log.append({
                    'op': op,
                    'char': char,
                    'timestamp_ms': current_time
                })
                
                if not self.dry_run:
                    await self.type_character(char, current_time)
                
                # Estimate time for typing (will be refined by timing_engine)
                current_time += 150
            
            elif op == 'PAUSE':
                pause_ms = data.get('ms', 0)
                execution_log.append({
                    'op': op,
                    'duration_ms': pause_ms,
                    'timestamp_ms': current_time
                })
                current_time += pause_ms
            
            elif op == 'DELETE':
                count = data
                execution_log.append({
                    'op': op,
                    'count': count,
                    'timestamp_ms': current_time
                })
                
                if not self.dry_run:
                    await self.delete_characters(count, current_time)
                
                current_time += count * 150
            
            elif op == 'SESSION_GAP':
                hours = data.get('hours', 0)
                execution_log.append({
                    'op': op,
                    'hours': hours,
                    'timestamp_ms': current_time,
                    'note': 'Session gap - would resume later in realistic mode'
                })
                # Don't advance current_time for gaps in immediate mode
        
        # Write execution log
        result = {
            'executed': not self.dry_run,
            'total_events': len(script),
            'total_time_ms': current_time,
            'log': execution_log
        }
        
        if output_file:
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2)
        
        return result
    
    async def navigate_to_docs(self, doc_id: str):
        """Navigate to Google Docs document."""
        if self.dry_run:
            return
        
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        await self.page.goto(url)
        
        # Wait for editor to load
        await self.page.wait_for_selector('.kix-appview-editor', timeout=30000)
        
        # Focus the editor
        await self.page.click('.kix-appview-editor')

def run_test():
    print("--- CDP Emitter Test ---")
    print(f"Playwright Available: {PLAYWRIGHT_AVAILABLE}")
    
    emitter = CDPEmitter(dry_run=True)
    
    # Sample script
    test_script = [
        {'op': 'TYPE', 'data': 'H'},
        {'op': 'TYPE', 'data': 'e'},
        {'op': 'PAUSE', 'data': {'ms': 100}},
        {'op': 'TYPE', 'data': 'l'},
        {'op': 'TYPE', 'data': 'l'},
        {'op': 'TYPE', 'data': 'o'},
    ]
    
    result = asyncio.run(emitter.execute_script(test_script, output_file='/tmp/cdp_test.json'))
    
    print(f"Dry Run: {result['executed'] == False}")
    print(f"Total Events: {result['total_events']}")
    print(f"Total Time: {result['total_time_ms']:.1f} ms")
    print(f"Log written to: /tmp/cdp_test.json")
    
    # Verify file
    import json
    with open('/tmp/cdp_test.json', 'r') as f:
        saved = json.load(f)
    print(f"Saved Log Events: {len(saved['log'])}")
    
    return True

if __name__ == "__main__":
    run_test()
