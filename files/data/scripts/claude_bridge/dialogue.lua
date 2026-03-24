-- Dialogue handling module for the Claude bridge.
-- Tracks dialogue state and provides functions for reading NPC conversations.

local core = require('openmw.core')
local types = require('openmw.types')
local ui = require('openmw.ui')

local dialogue = {}

-- Current dialogue state
local currentDialogue = nil
local dialogueHistory = {}  -- recent dialogue entries this conversation
local MAX_HISTORY = 20

-- Check if dialogue UI is currently open
function dialogue.isDialogueOpen()
    local ok, stack = pcall(ui._getUiModeStack)
    if not ok or not stack then return false end
    for _, mode in ipairs(stack) do
        if mode == 'Dialogue' then
            return true
        end
    end
    return false
end

-- Look up dialogue text by record and info ID
local function lookupDialogueText(dialogueType, recordId, infoId)
    local store = nil
    if dialogueType == 'topic' then
        store = core.dialogue.topic
    elseif dialogueType == 'greeting' then
        store = core.dialogue.greeting
    elseif dialogueType == 'persuasion' then
        store = core.dialogue.persuasion
    elseif dialogueType == 'journal' then
        store = core.dialogue.journal
    elseif dialogueType == 'voice' then
        store = core.dialogue.voice
    end

    if not store then return nil end

    -- Search through records for matching info
    local ok, records = pcall(function() return store.records end)
    if not ok or not records then return nil end

    for _, record in ipairs(records) do
        if record.id == recordId then
            for _, info in ipairs(record.infos) do
                if info.id == infoId then
                    return info.text
                end
            end
        end
    end
    return nil
end

-- Get the NPC name from an actor object
local function getActorName(actor)
    local ok, record = pcall(function()
        if types.NPC.objectIsInstance(actor) then
            return types.NPC.record(actor)
        elseif types.Creature.objectIsInstance(actor) then
            return types.Creature.record(actor)
        end
        return nil
    end)
    if ok and record and record.name then
        return record.name
    end
    return actor.recordId or 'unknown'
end

-- Get list of known dialogue topics for the player
function dialogue.getKnownTopics(playerObj)
    local topics = {}
    local ok, journal = pcall(function() return types.Player.journal(playerObj) end)
    if ok and journal and journal.topics then
        for topicName, _ in pairs(journal.topics) do
            topics[#topics + 1] = topicName
        end
    end
    table.sort(topics)
    return topics
end

-- Handle the DialogueResponse engine event
-- Called from player.lua's eventHandlers
-- Returns a message table to send over the bridge, or nil
function dialogue.handleDialogueResponse(data)
    local actor = data.actor
    local dialogueType = data.type
    local infoId = data.infoId
    local recordId = data.recordId

    -- Look up the actual text
    local text = lookupDialogueText(dialogueType, recordId, infoId)

    local npcName = getActorName(actor)

    local entry = {
        npc = npcName,
        npcId = actor.recordId,
        dialogueType = dialogueType,
        recordId = recordId,
        text = text or '(text not found)',
    }

    -- Update current dialogue state
    currentDialogue = {
        npc = npcName,
        npcId = actor.recordId,
        actor = actor,
    }

    -- Add to history
    dialogueHistory[#dialogueHistory + 1] = entry
    if #dialogueHistory > MAX_HISTORY then
        table.remove(dialogueHistory, 1)
    end

    return {
        type = 'dialogue',
        npc = npcName,
        npcId = actor.recordId,
        dialogueType = dialogueType,
        topic = recordId,
        text = text or '(text not found)',
    }
end

-- Build a dialogue state summary for observations
-- Returns nil if no dialogue is active
function dialogue.getDialogueState(playerObj)
    if not dialogue.isDialogueOpen() then
        currentDialogue = nil
        return nil
    end

    if not currentDialogue then
        return {active = true, npc = 'unknown', topics = {}, history = {}}
    end

    local topics = dialogue.getKnownTopics(playerObj)

    -- Build recent history for context
    local recentHistory = {}
    local startIdx = math.max(1, #dialogueHistory - 5)
    for i = startIdx, #dialogueHistory do
        local h = dialogueHistory[i]
        recentHistory[#recentHistory + 1] = {
            dialogueType = h.dialogueType,
            topic = h.recordId,
            text = h.text,
        }
    end

    return {
        active = true,
        npc = currentDialogue.npc,
        npcId = currentDialogue.npcId,
        topics = topics,
        history = recentHistory,
    }
end

-- Clear dialogue state (called when dialogue closes)
function dialogue.clearDialogue()
    currentDialogue = nil
    dialogueHistory = {}
end

return dialogue
