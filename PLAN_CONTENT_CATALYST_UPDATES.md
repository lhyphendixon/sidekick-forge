# Content Catalyst Updates Plan

## Overview
Update the Content Catalyst widget to:
1. Remove the "Text Topic" button and make text instructions available for ALL input types
2. Add a new "Knowledge Base Document" button that opens a document selector popup

---

## Current State

### Existing Tabs (3 buttons):
- **Text Topic** (💬) - Textarea for topic/content input
- **URL** (🔗) - URL input field
- **Audio** (🎙️) - Audio file upload

### Files to Modify:
- `/root/sidekick-forge/app/static/js/content-catalyst-widget.js` - Main widget logic
- `/root/sidekick-forge/app/static/css/widgets.css` - Styling
- `/root/sidekick-forge/app/api/v1/content_catalyst.py` - API endpoints (new endpoint for document list)
- `/root/sidekick-forge/app/services/content_catalyst_service.py` - Service layer (if needed)

---

## Implementation Plan

### Phase 1: Restructure Input UI

**Goal:** Remove "Text Topic" as a separate tab, make text instructions always visible

**Changes to `content-catalyst-widget.js`:**

1. **Remove "Text Topic" tab button** (line ~64)
   - Delete the `💬 Text Topic` button from the tabs

2. **New Tab Structure (2 buttons + 1 new):**
   ```
   [📄 Document] [🔗 URL] [🎙️ Audio]
   ```

3. **Always-visible text instruction field:**
   - Add a textarea labeled "Instructions (optional)" above or below the source-specific input
   - This field appears regardless of which tab is selected
   - Placeholder: "Describe what you want to create, any specific angle, or additional context..."

4. **Update tab switching logic** (`switchSourceType()` method):
   - Remove `text` case
   - Add `document` case
   - Ensure text instructions field persists across tab switches

### Phase 2: Add Document Selector Button & Popup

**Goal:** Create a new "Document" tab that opens a slide-up document picker

**UI Components:**

1. **New Tab Button:**
   ```html
   <button type="button" class="cc-tab" data-source="document">
       📄 Document
   </button>
   ```

2. **Document Selector Popup (Slide-up Modal):**
   ```html
   <div class="cc-document-picker">
       <div class="cc-document-picker-header">
           <h3>Select a Document</h3>
           <button class="cc-document-picker-close">×</button>
       </div>
       <div class="cc-document-picker-search">
           <input type="text" placeholder="Search documents..." />
       </div>
       <div class="cc-document-picker-list">
           <!-- Document items rendered here -->
       </div>
   </div>
   ```

3. **Document List Item:**
   ```html
   <div class="cc-document-item" data-doc-id="123">
       <div class="cc-document-icon">📄</div>
       <div class="cc-document-info">
           <div class="cc-document-title">Document Title</div>
           <div class="cc-document-meta">Added: Jan 14, 2026</div>
       </div>
   </div>
   ```

### Phase 3: Backend API for Document List

**New Endpoint:** `GET /api/v1/content-catalyst/documents/{agent_id}`

**File:** `/root/sidekick-forge/app/api/v1/content_catalyst.py`

```python
@router.get("/documents/{agent_id}")
async def get_agent_documents(
    agent_id: str,
    current_user: dict = Depends(get_current_user_optional),
):
    """Get list of documents assigned to an agent for Content Catalyst selection."""
    # Query agent_documents join table
    # Return list of documents with id, title, created_at
    pass
```

**Response Format:**
```json
{
    "documents": [
        {
            "id": "uuid",
            "title": "Document Title",
            "created_at": "2026-01-14T00:00:00Z",
            "type": "transcript|pdf|text"
        }
    ]
}
```

### Phase 4: CSS Styling

**File:** `/root/sidekick-forge/app/static/css/widgets.css`

**New Styles:**

1. **Document Picker Overlay:**
   ```css
   .cc-document-picker-overlay {
       position: absolute;
       bottom: 0;
       left: 0;
       right: 0;
       top: 0;
       background: rgba(0, 0, 0, 0.5);
       opacity: 0;
       pointer-events: none;
       transition: opacity 0.3s ease;
   }

   .cc-document-picker-overlay.show {
       opacity: 1;
       pointer-events: auto;
   }
   ```

2. **Slide-up Animation:**
   ```css
   .cc-document-picker {
       position: absolute;
       bottom: 0;
       left: 0;
       right: 0;
       max-height: 70%;
       background: rgba(20, 20, 20, 0.95);
       border-top: 1px solid rgba(255, 255, 255, 0.1);
       border-radius: 16px 16px 0 0;
       transform: translateY(100%);
       transition: transform 0.3s ease;
       overflow: hidden;
       display: flex;
       flex-direction: column;
   }

   .cc-document-picker.show {
       transform: translateY(0);
   }
   ```

3. **Document List Items:**
   ```css
   .cc-document-item {
       display: flex;
       align-items: center;
       gap: 12px;
       padding: 12px 16px;
       border-bottom: 1px solid rgba(255, 255, 255, 0.05);
       cursor: pointer;
       transition: background 0.2s ease;
   }

   .cc-document-item:hover {
       background: rgba(1, 164, 166, 0.1);
   }

   .cc-document-item.selected {
       background: rgba(1, 164, 166, 0.2);
       border-left: 3px solid #01a4a6;
   }
   ```

### Phase 5: JavaScript Logic

**New Methods in `ContentCatalystWidget` class:**

1. **`openDocumentPicker()`** - Opens the slide-up picker
2. **`closeDocumentPicker()`** - Closes with animation
3. **`loadDocuments()`** - Fetches documents from API
4. **`renderDocumentList(documents)`** - Renders the document items
5. **`selectDocument(docId, docTitle)`** - Handles document selection
6. **`filterDocuments(searchTerm)`** - Client-side search filtering

**Event Flow:**
1. User clicks "📄 Document" tab
2. `openDocumentPicker()` is called
3. `loadDocuments()` fetches from API (with loading spinner)
4. Documents render in scrollable list
5. User can search/filter documents
6. User clicks a document → `selectDocument()` stores selection
7. Picker closes, selected document shown in UI
8. Text instructions field remains visible for additional context

### Phase 6: Integration with Generation

**Update `startGeneration()` method:**

- Add new source type handling for `document`
- Pass document ID and title to backend
- Backend fetches document content from knowledge base
- Document content used as primary source material

**API Request Body Update:**
```json
{
    "source_type": "document",
    "document_id": "uuid",
    "document_title": "Document Title",
    "text_instructions": "User's additional instructions...",
    "word_count": 1500,
    "style": "default"
}
```

---

## UI Mockup (ASCII)

### Before:
```
┌─────────────────────────────────────┐
│  [💬 Text Topic] [🔗 URL] [🎙️ Audio] │
├─────────────────────────────────────┤
│  (Input area based on selected tab) │
└─────────────────────────────────────┘
```

### After:
```
┌─────────────────────────────────────┐
│  [📄 Document] [🔗 URL] [🎙️ Audio]  │
├─────────────────────────────────────┤
│  Instructions (optional)            │
│  ┌─────────────────────────────────┐│
│  │ Describe what you want...       ││
│  └─────────────────────────────────┘│
├─────────────────────────────────────┤
│  (Source-specific input below)      │
│  - Document: "Selected: Doc Title"  │
│  - URL: URL input field             │
│  - Audio: File upload               │
└─────────────────────────────────────┘
```

### Document Picker (Slide-up):
```
┌─────────────────────────────────────┐
│         Select a Document        ✕  │
├─────────────────────────────────────┤
│  🔍 Search documents...             │
├─────────────────────────────────────┤
│  📄 New Recording 249               │
│     Added: Jan 14, 2026             │
├─────────────────────────────────────┤
│  📄 New Recording 233               │
│     Added: Jan 13, 2026             │
├─────────────────────────────────────┤
│  📄 Divine Plan Document            │
│     Added: Jan 10, 2026             │
└─────────────────────────────────────┘
```

---

## Implementation Order

1. **Backend First:** Add `/documents/{agent_id}` API endpoint
2. **CSS Second:** Add all new styles for document picker
3. **JS Third:**
   - Restructure tabs (remove Text Topic, add Document)
   - Add always-visible text instructions field
   - Implement document picker popup with slide animation
   - Add document selection logic
4. **Integration:** Update `startGeneration()` to handle document source type
5. **Testing:** Verify with actual agent documents

---

## Estimated Changes

| File | Lines Added/Modified |
|------|---------------------|
| `content-catalyst-widget.js` | ~150 lines |
| `widgets.css` | ~80 lines |
| `content_catalyst.py` (API) | ~40 lines |
| Template version bump | 1 line |

---

## Notes

- The document picker should be contained within the embed iframe
- Use existing glass morphism styling to match current design
- Document search is client-side filtering (no additional API calls)
- Consider caching document list for the session
- Slide-up animation should be smooth (0.3s ease)
