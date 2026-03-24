local json = {}

-- Encode a Lua value to a JSON string
function json.encode(value)
    local vtype = type(value)
    if value == nil then
        return 'null'
    elseif vtype == 'boolean' then
        return value and 'true' or 'false'
    elseif vtype == 'number' then
        -- Handle NaN and Inf
        if value ~= value then return 'null' end
        if value == math.huge or value == -math.huge then return 'null' end
        -- Use integer format when possible
        if value == math.floor(value) and value >= -2^53 and value <= 2^53 then
            return string.format('%d', value)
        end
        return string.format('%.14g', value)
    elseif vtype == 'string' then
        return json.encodeString(value)
    elseif vtype == 'table' then
        return json.encodeTable(value)
    else
        return 'null'
    end
end

function json.encodeString(s)
    -- Escape special characters
    local escaped = s:gsub('[\\"\x00-\x1f]', function(c)
        local replacements = {
            ['\\'] = '\\\\',
            ['"'] = '\\"',
            ['\n'] = '\\n',
            ['\r'] = '\\r',
            ['\t'] = '\\t',
            ['\b'] = '\\b',
            ['\f'] = '\\f',
        }
        return replacements[c] or string.format('\\u%04x', string.byte(c))
    end)
    return '"' .. escaped .. '"'
end

function json.encodeTable(t)
    -- Determine if table is an array (sequential integer keys starting at 1)
    local isArray = true
    local maxIndex = 0
    local count = 0
    for k, _ in pairs(t) do
        count = count + 1
        if type(k) == 'number' and k == math.floor(k) and k >= 1 then
            if k > maxIndex then maxIndex = k end
        else
            isArray = false
            break
        end
    end
    if isArray and maxIndex ~= count then
        isArray = false
    end
    -- Empty table: default to object
    if count == 0 then
        return '{}'
    end

    if isArray then
        local parts = {}
        for i = 1, maxIndex do
            parts[i] = json.encode(t[i])
        end
        return '[' .. table.concat(parts, ',') .. ']'
    else
        local parts = {}
        for k, v in pairs(t) do
            local key = type(k) == 'string' and k or tostring(k)
            parts[#parts + 1] = json.encodeString(key) .. ':' .. json.encode(v)
        end
        return '{' .. table.concat(parts, ',') .. '}'
    end
end

-- Decode a JSON string to a Lua value
-- Minimal decoder: handles objects, arrays, strings, numbers, booleans, null
function json.decode(str)
    local pos = 1

    local function skipWhitespace()
        pos = str:find('[^ \t\r\n]', pos) or (#str + 1)
    end

    local function peek()
        skipWhitespace()
        return str:sub(pos, pos)
    end

    local parseValue -- forward declaration

    local function parseString()
        -- pos should be at opening quote
        pos = pos + 1  -- skip "
        local start = pos
        local parts = {}
        while pos <= #str do
            local c = str:sub(pos, pos)
            if c == '"' then
                parts[#parts + 1] = str:sub(start, pos - 1)
                pos = pos + 1
                return table.concat(parts)
            elseif c == '\\' then
                parts[#parts + 1] = str:sub(start, pos - 1)
                pos = pos + 1
                local esc = str:sub(pos, pos)
                if esc == '"' then parts[#parts + 1] = '"'
                elseif esc == '\\' then parts[#parts + 1] = '\\'
                elseif esc == '/' then parts[#parts + 1] = '/'
                elseif esc == 'n' then parts[#parts + 1] = '\n'
                elseif esc == 'r' then parts[#parts + 1] = '\r'
                elseif esc == 't' then parts[#parts + 1] = '\t'
                elseif esc == 'b' then parts[#parts + 1] = '\b'
                elseif esc == 'f' then parts[#parts + 1] = '\f'
                elseif esc == 'u' then
                    local hex = str:sub(pos + 1, pos + 4)
                    local code = tonumber(hex, 16)
                    if code then
                        -- Simple: only handle BMP, output as UTF-8
                        if code < 0x80 then
                            parts[#parts + 1] = string.char(code)
                        elseif code < 0x800 then
                            parts[#parts + 1] = string.char(0xC0 + math.floor(code / 64), 0x80 + (code % 64))
                        else
                            parts[#parts + 1] = string.char(0xE0 + math.floor(code / 4096), 0x80 + math.floor((code % 4096) / 64), 0x80 + (code % 64))
                        end
                    end
                    pos = pos + 4
                end
                pos = pos + 1
                start = pos
            else
                pos = pos + 1
            end
        end
        error('json.decode: unterminated string')
    end

    local function parseNumber()
        local startPos = pos
        -- Match JSON number pattern
        if str:sub(pos, pos) == '-' then pos = pos + 1 end
        while str:sub(pos, pos):match('[0-9]') do pos = pos + 1 end
        if str:sub(pos, pos) == '.' then
            pos = pos + 1
            while str:sub(pos, pos):match('[0-9]') do pos = pos + 1 end
        end
        if str:sub(pos, pos):match('[eE]') then
            pos = pos + 1
            if str:sub(pos, pos):match('[%+%-]') then pos = pos + 1 end
            while str:sub(pos, pos):match('[0-9]') do pos = pos + 1 end
        end
        local numStr = str:sub(startPos, pos - 1)
        return tonumber(numStr)
    end

    local function parseArray()
        pos = pos + 1 -- skip [
        local arr = {}
        skipWhitespace()
        if str:sub(pos, pos) == ']' then
            pos = pos + 1
            return arr
        end
        while true do
            arr[#arr + 1] = parseValue()
            skipWhitespace()
            local c = str:sub(pos, pos)
            if c == ']' then
                pos = pos + 1
                return arr
            elseif c == ',' then
                pos = pos + 1
            else
                error('json.decode: expected , or ] in array')
            end
        end
    end

    local function parseObject()
        pos = pos + 1 -- skip {
        local obj = {}
        skipWhitespace()
        if str:sub(pos, pos) == '}' then
            pos = pos + 1
            return obj
        end
        while true do
            skipWhitespace()
            if str:sub(pos, pos) ~= '"' then
                error('json.decode: expected string key in object')
            end
            local key = parseString()
            skipWhitespace()
            if str:sub(pos, pos) ~= ':' then
                error('json.decode: expected : after key')
            end
            pos = pos + 1
            obj[key] = parseValue()
            skipWhitespace()
            local c = str:sub(pos, pos)
            if c == '}' then
                pos = pos + 1
                return obj
            elseif c == ',' then
                pos = pos + 1
            else
                error('json.decode: expected , or } in object')
            end
        end
    end

    parseValue = function()
        skipWhitespace()
        local c = str:sub(pos, pos)
        if c == '"' then return parseString()
        elseif c == '{' then return parseObject()
        elseif c == '[' then return parseArray()
        elseif c == 't' then
            pos = pos + 4 -- true
            return true
        elseif c == 'f' then
            pos = pos + 5 -- false
            return false
        elseif c == 'n' then
            pos = pos + 4 -- null
            return nil
        elseif c == '-' or c:match('[0-9]') then
            return parseNumber()
        else
            error('json.decode: unexpected character at position ' .. pos .. ': ' .. c)
        end
    end

    local result = parseValue()
    return result
end

return json
