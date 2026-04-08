#include "bridgebindings.hpp"

#include <osg/Image>
#include <osgDB/WriteFile>

#include <components/debug/debuglog.hpp>
#include <components/lua/bridgesocket.hpp>
#include <components/lua/luastate.hpp>

#include "../mwbase/dialoguemanager.hpp"
#include "../mwbase/environment.hpp"
#include "../mwbase/statemanager.hpp"
#include "../mwstate/character.hpp"
#include "../mwbase/mechanicsmanager.hpp"
#include "../mwbase/windowmanager.hpp"
#include "../mwbase/world.hpp"
#include "../mwgui/dialogue.hpp"
#include "../mwgui/mode.hpp"
#include "../mwgui/windowmanagerimp.hpp"
#include "../mwmechanics/actorutil.hpp"
#include "../mwmechanics/alchemy.hpp"
#include "../mwmechanics/creaturestats.hpp"
#include "../mwmechanics/magiceffects.hpp"
#include "../mwmechanics/npcstats.hpp"
#include "../mwmechanics/security.hpp"
#include "../mwworld/actionteleport.hpp"
#include "../mwworld/cellstore.hpp"
#include "../mwworld/class.hpp"
#include "../mwworld/containerstore.hpp"
#include "../mwworld/esmstore.hpp"
#include "../mwworld/worldmodel.hpp"
#include <components/esm/util.hpp>
#include <components/esm3/loadcell.hpp>
#include <components/esm3/loadcont.hpp>
#include <components/esm3/loadcrea.hpp>
#include <components/esm3/loaddoor.hpp>
#include <components/esm3/loadappa.hpp>
#include <components/esm3/loadgmst.hpp>
#include <components/esm3/loadingr.hpp>
#include <components/esm3/loadlock.hpp>
#include <components/esm3/loadmgef.hpp>
#include <components/esm3/loadnpc.hpp>
#include <components/esm3/loadprob.hpp>
#include <components/esm3/loadskil.hpp>
#include <components/esm3/transport.hpp>
#include <components/settings/values.hpp>
#include "context.hpp"
#include "luamanagerimp.hpp"

namespace MWLua
{
    // Single shared bridge socket instance
    static std::unique_ptr<LuaUtil::BridgeSocket> sBridgeSocket;

    bool isBridgeConnected()
    {
        return sBridgeSocket && sBridgeSocket->isConnected();
    }

    // ResponseCallback that captures dialogue text AND forwards to the GUI dialogue window
    class BridgeResponseCallback : public MWBase::DialogueManager::ResponseCallback
    {
    public:
        std::string mTitle;
        std::string mText;
        bool mHasResponse = false;
        MWBase::DialogueManager::ResponseCallback* mGuiCallback = nullptr;

        void addResponse(std::string_view title, std::string_view text) override
        {
            mTitle = title;
            mText = text;
            mHasResponse = true;
            // Also forward to the GUI so the dialogue window updates
            if (mGuiCallback)
                mGuiCallback->addResponse(title, text);
        }
    };

    // Get the GUI's dialogue callback by accessing WindowManagerImp's dialogue window
    static MWBase::DialogueManager::ResponseCallback* getGuiDialogueCallback()
    {
        auto* winMgr = dynamic_cast<MWGui::WindowManager*>(&*MWBase::Environment::get().getWindowManager());
        if (!winMgr)
            return nullptr;
        auto* dlgWin = winMgr->getDialogueWindow();
        if (!dlgWin)
            return nullptr;
        return dlgWin->getCallback();
    }

    sol::table initBridgePackage(const Context& context)
    {
        sol::state_view lua = context.sol();
        sol::table api(lua, sol::create);

        api["start"] = [](uint16_t port) {
            if (!sBridgeSocket)
                sBridgeSocket = std::make_unique<LuaUtil::BridgeSocket>();
            sBridgeSocket->start(port);
        };

        api["stop"] = []() {
            if (sBridgeSocket)
                sBridgeSocket->stop();
        };

        api["isConnected"] = []() -> bool {
            return sBridgeSocket && sBridgeSocket->isConnected();
        };

        api["send"] = [](std::string_view msg) {
            if (sBridgeSocket)
                sBridgeSocket->send(msg);
        };

        api["poll"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            if (sBridgeSocket)
            {
                sBridgeSocket->update();
                auto messages = sBridgeSocket->poll();
                for (size_t i = 0; i < messages.size(); ++i)
                    result[i + 1] = std::move(messages[i]);
            }
            return result;
        };

        api["getPort"] = []() -> uint16_t {
            return sBridgeSocket ? sBridgeSocket->getPort() : 0;
        };

        api["screenshot"] = [luaManager = context.mLuaManager](
                                 const std::string& path, sol::optional<int> width, sol::optional<int> height) {
            int w = width.value_or(640);
            int h = height.value_or(480);
            std::string savePath = path;

            luaManager->addAction(
                [savePath, w, h]()
                {
                    try
                    {
                        osg::ref_ptr<osg::Image> image(new osg::Image);
                        MWBase::Environment::get().getWorld()->screenshot(image.get(), w, h);
                        osgDB::writeImageFile(*image, savePath);
                        Log(Debug::Info) << "Bridge: Screenshot saved to " << savePath;

                        if (sBridgeSocket && sBridgeSocket->isConnected())
                        {
                            std::string msg = R"({"type":"screenshot","path":")" + savePath
                                + R"(","width":)" + std::to_string(w)
                                + R"(,"height":)" + std::to_string(h) + "}";
                            sBridgeSocket->send(msg);
                            sBridgeSocket->update();
                        }
                    }
                    catch (const std::exception& e)
                    {
                        Log(Debug::Error) << "Bridge: Screenshot failed: " << e.what();
                        if (sBridgeSocket && sBridgeSocket->isConnected())
                        {
                            std::string msg = R"({"type":"screenshot_error","message":")"
                                + std::string(e.what()) + R"("})";
                            sBridgeSocket->send(msg);
                            sBridgeSocket->update();
                        }
                    }
                },
                "BridgeScreenshot");
        };

        // --- Travel bindings ---

        // Get travel destinations from a nearby NPC by name.
        // Returns a table of {name, price, interior, pos={x,y,z}} or nil if NPC not found.
        api["getTravelDestinations"] = [luaState = context.mLua](std::string_view npcName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
                return result;

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find the NPC near the player by name
            MWWorld::Ptr npc;
            std::string searchName(npcName);
            std::transform(searchName.begin(), searchName.end(), searchName.begin(), ::tolower);

            // Search nearby actors for the named NPC
            std::vector<MWWorld::Ptr> actors;
            MWBase::Environment::get().getMechanicsManager()->getActorsInRange(
                player.getRefData().getPosition().asVec3(), 1000.0f, actors);
            for (const auto& actor : actors)
            {
                if (actor == player)
                    continue;
                std::string actorName(actor.getClass().getName(actor));
                std::string actorNameLower = actorName;
                std::transform(actorNameLower.begin(), actorNameLower.end(), actorNameLower.begin(), ::tolower);
                if (actorNameLower.find(searchName) != std::string::npos)
                {
                    npc = actor;
                    break;
                }
            }

            if (npc.isEmpty())
                return result;

            // Get transport destinations
            std::vector<ESM::Transport::Dest> transport;
            if (npc.getClass().isNpc())
                transport = npc.get<ESM::NPC>()->mBase->getTransport();
            else if (npc.getType() == ESM::Creature::sRecordId)
                transport = npc.get<ESM::Creature>()->mBase->getTransport();

            if (transport.empty())
                return result;

            const MWWorld::Store<ESM::GameSetting>& gmst
                = MWBase::Environment::get().getESMStore()->get<ESM::GameSetting>();
            const MWWorld::WorldModel& worldModel = *MWBase::Environment::get().getWorldModel();

            int idx = 1;
            for (const auto& dest : transport)
            {
                std::string cellname(dest.mCellName);
                bool interior = true;

                if (cellname.empty())
                {
                    const ESM::ExteriorCellLocation cellIndex
                        = ESM::positionToExteriorCellLocation(dest.mPos.pos[0], dest.mPos.pos[1]);
                    MWWorld::CellStore& cell = worldModel.getExterior(cellIndex);
                    cellname = world->getCellName(&cell);
                    interior = false;
                }
                else
                {
                    const MWWorld::CellStore* destCell = worldModel.findCell(cellname, false);
                    if (!destCell)
                        continue;
                    interior = !destCell->getCell()->isExterior();
                }

                // Calculate price (same logic as TravelWindow)
                int price;
                if (!npc.getCell()->isExterior())
                {
                    price = gmst.find("fMagesGuildTravel")->mValue.getInteger();
                }
                else
                {
                    const ESM::Position playerPos = player.getRefData().getPosition();
                    double d = std::sqrt(
                        std::pow(dest.mPos.pos[0] - playerPos.pos[0], 2)
                        + std::pow(dest.mPos.pos[1] - playerPos.pos[1], 2)
                        + std::pow(dest.mPos.pos[2] - playerPos.pos[2], 2));
                    float fTravelMult = gmst.find("fTravelMult")->mValue.getFloat();
                    price = (fTravelMult != 0) ? static_cast<int>(d / fTravelMult) : static_cast<int>(d);
                }

                std::set<MWWorld::Ptr> followers;
                MWWorld::ActionTeleport::getFollowers(player, followers, !interior);
                price *= 1 + static_cast<int>(followers.size());
                price = std::max(1, price);
                price = MWBase::Environment::get().getMechanicsManager()->getBarterOffer(npc, price, true);

                sol::table entry(lua2, sol::create);
                entry["name"] = cellname;
                entry["price"] = price;
                entry["interior"] = interior;
                sol::table pos(lua2, sol::create);
                pos["x"] = dest.mPos.pos[0];
                pos["y"] = dest.mPos.pos[1];
                pos["z"] = dest.mPos.pos[2];
                entry["pos"] = pos;
                result[idx++] = entry;
            }

            return result;
        };

        // Use a travel service: pay gold and teleport to a destination.
        // npcName: name of the travel NPC (must be nearby)
        // destName: name of the destination cell (must match one of the NPC's destinations)
        // Returns {success, message}
        api["travel"] = [luaState = context.mLua, luaManager = context.mLuaManager](
                             std::string_view npcName, std::string_view destName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find NPC
            std::string searchName(npcName);
            std::transform(searchName.begin(), searchName.end(), searchName.begin(), ::tolower);

            MWWorld::Ptr npc;
            std::vector<MWWorld::Ptr> actors;
            MWBase::Environment::get().getMechanicsManager()->getActorsInRange(
                player.getRefData().getPosition().asVec3(), 1000.0f, actors);
            for (const auto& actor : actors)
            {
                if (actor == player)
                    continue;
                std::string actorName(actor.getClass().getName(actor));
                std::string actorNameLower = actorName;
                std::transform(actorNameLower.begin(), actorNameLower.end(), actorNameLower.begin(), ::tolower);
                if (actorNameLower.find(searchName) != std::string::npos)
                {
                    npc = actor;
                    break;
                }
            }

            if (npc.isEmpty())
            {
                result["success"] = false;
                result["message"] = "Travel NPC not found nearby: " + std::string(npcName);
                return result;
            }

            // Get transport list
            std::vector<ESM::Transport::Dest> transport;
            if (npc.getClass().isNpc())
                transport = npc.get<ESM::NPC>()->mBase->getTransport();
            else if (npc.getType() == ESM::Creature::sRecordId)
                transport = npc.get<ESM::Creature>()->mBase->getTransport();

            if (transport.empty())
            {
                result["success"] = false;
                result["message"] = std::string(npc.getClass().getName(npc)) + " does not offer travel services";
                return result;
            }

            // Find matching destination
            std::string searchDest(destName);
            std::transform(searchDest.begin(), searchDest.end(), searchDest.begin(), ::tolower);

            const MWWorld::Store<ESM::GameSetting>& gmst
                = MWBase::Environment::get().getESMStore()->get<ESM::GameSetting>();
            const MWWorld::WorldModel& worldModel = *MWBase::Environment::get().getWorldModel();

            for (const auto& dest : transport)
            {
                std::string cellname(dest.mCellName);
                bool interior = true;

                if (cellname.empty())
                {
                    const ESM::ExteriorCellLocation cellIndex
                        = ESM::positionToExteriorCellLocation(dest.mPos.pos[0], dest.mPos.pos[1]);
                    MWWorld::CellStore& cell = worldModel.getExterior(cellIndex);
                    cellname = world->getCellName(&cell);
                    interior = false;
                }
                else
                {
                    const MWWorld::CellStore* destCell = worldModel.findCell(cellname, false);
                    if (!destCell)
                        continue;
                    interior = !destCell->getCell()->isExterior();
                }

                std::string cellnameLower = cellname;
                std::transform(cellnameLower.begin(), cellnameLower.end(), cellnameLower.begin(), ::tolower);
                if (cellnameLower.find(searchDest) == std::string::npos)
                    continue;

                // Found destination — calculate price
                int price;
                if (!npc.getCell()->isExterior())
                {
                    price = gmst.find("fMagesGuildTravel")->mValue.getInteger();
                }
                else
                {
                    const ESM::Position playerPos = player.getRefData().getPosition();
                    double d = std::sqrt(
                        std::pow(dest.mPos.pos[0] - playerPos.pos[0], 2)
                        + std::pow(dest.mPos.pos[1] - playerPos.pos[1], 2)
                        + std::pow(dest.mPos.pos[2] - playerPos.pos[2], 2));
                    float fTravelMult = gmst.find("fTravelMult")->mValue.getFloat();
                    price = (fTravelMult != 0) ? static_cast<int>(d / fTravelMult) : static_cast<int>(d);
                }

                std::set<MWWorld::Ptr> followers;
                MWWorld::ActionTeleport::getFollowers(player, followers, !interior);
                price *= 1 + static_cast<int>(followers.size());
                price = std::max(1, price);
                price = MWBase::Environment::get().getMechanicsManager()->getBarterOffer(npc, price, true);

                // Check if player can afford it
                int playerGold
                    = player.getClass().getContainerStore(player).count(MWWorld::ContainerStore::sGoldId);
                if (playerGold < price)
                {
                    result["success"] = false;
                    result["message"] = "Not enough gold. Need " + std::to_string(price) + ", have "
                        + std::to_string(playerGold);
                    return result;
                }

                // Execute travel (replicates TravelWindow::onTravelButtonClick)
                world->setPlayerTraveling(true);

                if (!npc.getCell()->isExterior())
                    MWBase::Environment::get().getWindowManager()->playSound(
                        ESM::RefId::stringRefId("mysticism cast"));

                player.getClass().getContainerStore(player).remove(MWWorld::ContainerStore::sGoldId, price);

                MWMechanics::CreatureStats& npcStats = npc.getClass().getCreatureStats(npc);
                npcStats.setGoldPool(npcStats.getGoldPool() + price);

                // Advance time for exterior travel
                if (npc.getCell()->isExterior())
                {
                    ESM::Position playerPos = player.getRefData().getPosition();
                    float d = (osg::Vec2f(dest.mPos.pos[0], dest.mPos.pos[1])
                        - osg::Vec2f(playerPos.pos[0], playerPos.pos[1]))
                                  .length();
                    float fTravelTimeMult = gmst.find("fTravelTimeMult")->mValue.getFloat();
                    int hours = static_cast<int>(d / fTravelTimeMult);
                    MWBase::Environment::get().getMechanicsManager()->rest(hours, true);
                    world->advanceTime(hours);
                }

                // Close any open dialogue/UI
                if (MWBase::Environment::get().getWindowManager()->containsMode(MWGui::GM_Dialogue))
                {
                    MWBase::Environment::get().getDialogueManager()->goodbyeSelected();
                    MWBase::Environment::get().getWindowManager()->removeGuiMode(MWGui::GM_Dialogue);
                }

                // Teleport
                MWBase::Environment::get().getWindowManager()->fadeScreenOut(1);
                const ESM::ExteriorCellLocation posCell
                    = ESM::positionToExteriorCellLocation(dest.mPos.pos[0], dest.mPos.pos[1]);
                ESM::RefId cellId = ESM::Cell::generateIdForCell(!interior, cellname, posCell.mX, posCell.mY);

                MWWorld::ActionTeleport action(cellId, dest.mPos, true);
                action.execute(player);

                MWBase::Environment::get().getWindowManager()->fadeScreenOut(0);
                MWBase::Environment::get().getWindowManager()->fadeScreenIn(1);

                result["success"] = true;
                result["message"] = "Traveled to " + cellname + " (cost: " + std::to_string(price) + " gold)";
                return result;
            }

            result["success"] = false;
            result["message"] = "Destination '" + std::string(destName) + "' not available from "
                + std::string(npc.getClass().getName(npc));
            return result;
        };

        // --- Dialogue bindings ---

        // Get the list of topics available with the current dialogue NPC.
        // Returns a Lua table of topic name strings.
        // Must be called while dialogue is open (after startDialogue/activateBy).
        api["getAvailableTopics"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            auto topics = MWBase::Environment::get().getDialogueManager()->getAvailableTopics();
            int i = 1;
            for (const auto& topic : topics)
                result[i++] = topic;
            return result;
        };

        // Select a dialogue topic by keyword. Returns {title, text} or {nil, errorMsg}.
        // This is the equivalent of clicking a topic in the dialogue window.
        api["selectDialogueTopic"] = [luaState = context.mLua](std::string_view keyword) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            BridgeResponseCallback callback;
            callback.mGuiCallback = getGuiDialogueCallback();
            MWBase::Environment::get().getDialogueManager()->keywordSelected(keyword, &callback);
            // Update the GUI topic list
            auto* guiCb = dynamic_cast<MWGui::ResponseCallback*>(callback.mGuiCallback);
            if (guiCb)
                guiCb->updateTopics();
            if (callback.mHasResponse)
            {
                result["title"] = callback.mTitle;
                result["text"] = callback.mText;
            }
            else
            {
                result[1] = sol::nil;
                result[2] = "No response for topic: " + std::string(keyword);
            }
            return result;
        };

        // --- Save/Load bindings ---

        api["quickSave"] = [](sol::optional<std::string> description) {
            std::string desc = description.value_or("Bridge quicksave");
            MWBase::Environment::get().getStateManager()->quickSave(desc);
        };

        api["quickLoad"] = []() {
            MWBase::Environment::get().getStateManager()->quickLoad();
        };

        // Save game to a new named slot.
        api["saveGame"] = [](std::string_view description) {
            MWBase::Environment::get().getStateManager()->saveGame(description);
        };

        // Load game by searching save slots for a matching description.
        // Returns true if a matching save was found and load was initiated.
        api["loadGame"] = [](std::string_view description) -> bool {
            auto* character = MWBase::Environment::get().getStateManager()->getCurrentCharacter();
            if (!character)
                return false;

            std::string searchDesc(description);
            std::transform(searchDesc.begin(), searchDesc.end(), searchDesc.begin(), ::tolower);

            for (auto it = character->begin(); it != character->end(); ++it)
            {
                std::string slotDesc = it->mProfile.mDescription;
                std::transform(slotDesc.begin(), slotDesc.end(), slotDesc.begin(), ::tolower);
                if (slotDesc.find(searchDesc) != std::string::npos)
                {
                    MWBase::Environment::get().getStateManager()->loadGame(character, it->mPath);
                    return true;
                }
            }
            return false;
        };

        api["listSaves"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            auto* character = MWBase::Environment::get().getStateManager()->getCurrentCharacter();
            if (!character)
                return result;

            int i = 1;
            for (auto it = character->begin(); it != character->end(); ++it)
            {
                sol::table entry(lua2, sol::create);
                entry["description"] = it->mProfile.mDescription;
                entry["playerName"] = it->mProfile.mPlayerName;
                entry["playerLevel"] = it->mProfile.mPlayerLevel;
                result[i++] = entry;
                if (i > 20)
                    break;  // cap at 20 saves
            }
            return result;
        };

        // Use (consume) an item from the player's inventory by record ID.
        // Works for potions, scrolls, ingredients, etc.
        api["useItem"] = [](std::string_view itemId) -> sol::table {
            auto world = MWBase::Environment::get().getWorld();
            MWWorld::Ptr player = world->getPlayerPtr();
            auto& store = player.getClass().getContainerStore(player);

            // Find the item in inventory
            for (auto it = store.begin(); it != store.end(); ++it)
            {
                if (it->getCellRef().getRefId() == ESM::RefId::stringRefId(itemId))
                {
                    auto action = it->getClass().use(*it);
                    action->execute(player);
                    return sol::table();
                }
            }
            return sol::table();
        };

        // Close the current dialogue. Equivalent to clicking "Goodbye".
        api["closeDialogue"] = []() {
            MWBase::Environment::get().getDialogueManager()->goodbyeSelected();
            MWBase::Environment::get().getWindowManager()->removeGuiMode(MWGui::GM_Dialogue);
        };

        // Check if dialogue UI is currently open.
        api["isDialogueOpen"] = []() -> bool {
            return MWBase::Environment::get().getWindowManager()->containsMode(MWGui::GM_Dialogue);
        };

        // Check if the NPC is presenting a choice (yes/no, multiple choice).
        api["isInChoice"] = []() -> bool {
            return MWBase::Environment::get().getDialogueManager()->isInChoice();
        };

        // Get the current dialogue choices. Returns a table of {text, id} pairs.
        api["getChoices"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            const auto& choices = MWBase::Environment::get().getDialogueManager()->getChoices();
            int i = 1;
            for (const auto& [text, id] : choices)
            {
                sol::table entry(lua2, sol::create);
                entry["text"] = text;
                entry["id"] = id;
                result[i++] = entry;
            }
            return result;
        };

        // Answer a dialogue choice by its ID. Returns {title, text} or {nil, errorMsg}.
        api["answerChoice"] = [luaState = context.mLua](int choiceId) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);
            BridgeResponseCallback callback;
            callback.mGuiCallback = getGuiDialogueCallback();
            MWBase::Environment::get().getDialogueManager()->questionAnswered(choiceId, &callback);
            // Update the GUI topic list
            auto* guiCb = dynamic_cast<MWGui::ResponseCallback*>(callback.mGuiCallback);
            if (guiCb)
                guiCb->updateTopics();
            if (callback.mHasResponse)
            {
                result["title"] = callback.mTitle;
                result["text"] = callback.mText;
            }
            else
            {
                result[1] = sol::nil;
                result[2] = "No response for choice";
            }
            return result;
        };

        // --- Auto Level-Up ---
        // Automatically levels up the player by picking the 3 attributes with the highest multipliers.
        // Bypasses the level-up dialog UI entirely.
        api["autoLevelUp"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();
            MWMechanics::NpcStats& pcStats = player.getClass().getNpcStats(player);

            // Check if level-up is actually available (progress >= 10 major/minor skill increases)
            int levelProgress = pcStats.getLevelProgress();
            if (levelProgress < 10)
            {
                result["success"] = false;
                result["message"] = "Not ready to level up (progress: "
                    + std::to_string(levelProgress) + "/10)";
                return result;
            }

            // Collect all attributes with their multipliers
            struct AttrInfo {
                ESM::Attribute::AttributeID id;
                std::string name;
                int multiplier;
                int currentBase;
            };
            std::vector<AttrInfo> attrs;

            const auto& attrStore = MWBase::Environment::get().getESMStore()->get<ESM::Attribute>();
            for (const ESM::Attribute& attribute : attrStore)
            {
                int mult = pcStats.getLevelupAttributeMultiplier(attribute.mId);
                int base = static_cast<int>(pcStats.getAttribute(attribute.mId).getBase());
                attrs.push_back({ attribute.mId, attribute.mName, mult, base });
            }

            // Sort by multiplier descending, then by lowest current value (prioritize weak attributes)
            std::sort(attrs.begin(), attrs.end(), [](const AttrInfo& a, const AttrInfo& b) {
                if (a.multiplier != b.multiplier)
                    return a.multiplier > b.multiplier;
                return a.currentBase < b.currentBase;
            });

            // Pick top 3 (skip attributes already at 100)
            std::vector<AttrInfo> chosen;
            for (const auto& attr : attrs)
            {
                if (chosen.size() >= 3)
                    break;
                if (attr.currentBase < 100)
                    chosen.push_back(attr);
            }

            if (chosen.size() < 3)
            {
                // Fill remaining with any non-maxed attributes
                for (const auto& attr : attrs)
                {
                    if (chosen.size() >= 3)
                        break;
                    if (attr.currentBase < 100
                        && std::find_if(chosen.begin(), chosen.end(),
                            [&](const AttrInfo& c) { return c.id == attr.id; }) == chosen.end())
                        chosen.push_back(attr);
                }
            }

            // Apply attribute increases
            sol::table increased(lua2, sol::create);
            int idx = 1;
            for (const auto& attr : chosen)
            {
                MWMechanics::AttributeValue attrVal = pcStats.getAttribute(attr.id);
                int newVal = std::min(100, static_cast<int>(attrVal.getBase()) + attr.multiplier);
                attrVal.setBase(newVal);
                pcStats.setAttribute(attr.id, attrVal);

                sol::table entry(lua2, sol::create);
                entry["name"] = attr.name;
                entry["increase"] = attr.multiplier;
                entry["newValue"] = newVal;
                increased[idx++] = entry;

                Log(Debug::Info) << "Bridge: Auto level-up: " << attr.name
                    << " +" << attr.multiplier << " -> " << newVal;
            }

            // Finalize level-up
            pcStats.levelUp();

            // Dismiss the level-up dialog if it's open
            if (MWBase::Environment::get().getWindowManager()->containsMode(MWGui::GM_Levelup))
                MWBase::Environment::get().getWindowManager()->removeGuiMode(MWGui::GM_Levelup);

            int newLevel = pcStats.getLevel();
            result["success"] = true;
            result["level"] = newLevel;
            result["increased"] = increased;
            result["message"] = "Leveled up to " + std::to_string(newLevel);

            Log(Debug::Info) << "Bridge: Player leveled up to " << newLevel;

            return result;
        };

        // --- Rest binding ---
        // Rest for a number of hours to recover health, magicka, and fatigue.
        api["rest"] = [luaState = context.mLua](int hours) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            if (hours < 1)
                hours = 1;
            if (hours > 8)
                hours = 8;

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            // Rest and advance time, one hour at a time (same pattern as WaitDialog)
            for (int i = 0; i < hours; ++i)
            {
                MWBase::Environment::get().getMechanicsManager()->rest(1, true);
                world->advanceTime(1);
            }

            result["success"] = true;
            result["message"] = "Rested for " + std::to_string(hours) + " hours";

            Log(Debug::Info) << "Bridge: Player rested for " << hours << " hours";

            return result;
        };

        // --- Persuasion binding ---
        // Persuade an NPC using admire, intimidate, taunt, or bribe.
        // Requires dialogue to be open with the NPC.
        api["persuade"] = [luaState = context.mLua](int persuasionType) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            // Validate persuasion type
            if (persuasionType < MWBase::MechanicsManager::PT_Admire
                || persuasionType > MWBase::MechanicsManager::PT_Bribe1000)
            {
                result["success"] = false;
                result["message"] = "Invalid persuasion type: " + std::to_string(persuasionType);
                return result;
            }

            // Check that dialogue is open
            if (!MWBase::Environment::get().getWindowManager()->containsMode(MWGui::GM_Dialogue))
            {
                result["success"] = false;
                result["message"] = "No dialogue is open — must be talking to an NPC";
                return result;
            }

            // For bribes, check player has enough gold
            MWWorld::Ptr player = MWBase::Environment::get().getWorld()->getPlayerPtr();
            int goldNeeded = 0;
            if (persuasionType == MWBase::MechanicsManager::PT_Bribe10)
                goldNeeded = 10;
            else if (persuasionType == MWBase::MechanicsManager::PT_Bribe100)
                goldNeeded = 100;
            else if (persuasionType == MWBase::MechanicsManager::PT_Bribe1000)
                goldNeeded = 1000;

            if (goldNeeded > 0)
            {
                int playerGold
                    = player.getClass().getContainerStore(player).count(MWWorld::ContainerStore::sGoldId);
                if (playerGold < goldNeeded)
                {
                    result["success"] = false;
                    result["message"] = "Not enough gold for bribe. Need " + std::to_string(goldNeeded)
                        + ", have " + std::to_string(playerGold);
                    return result;
                }
            }

            // Use the BridgeResponseCallback to capture the persuasion response
            BridgeResponseCallback callback;
            callback.mGuiCallback = getGuiDialogueCallback();

            MWBase::Environment::get().getDialogueManager()->persuade(persuasionType, &callback);

            // Update the GUI topic list
            auto* guiCb = dynamic_cast<MWGui::ResponseCallback*>(callback.mGuiCallback);
            if (guiCb)
                guiCb->updateTopics();

            // Get action name for the result message
            std::string actionName;
            if (persuasionType == MWBase::MechanicsManager::PT_Admire)
                actionName = "Admire";
            else if (persuasionType == MWBase::MechanicsManager::PT_Intimidate)
                actionName = "Intimidate";
            else if (persuasionType == MWBase::MechanicsManager::PT_Taunt)
                actionName = "Taunt";
            else
                actionName = "Bribe (" + std::to_string(goldNeeded) + " gold)";

            result["success"] = true;
            result["actionName"] = actionName;
            if (callback.mHasResponse)
            {
                result["text"] = callback.mText;
                result["message"] = actionName + ": " + callback.mText;
            }
            else
            {
                result["message"] = actionName + " performed";
            }

            Log(Debug::Info) << "Bridge: Persuasion — " << actionName;

            return result;
        };

        // --- Training bindings ---

        // Get training services from a nearby NPC by name.
        // Returns a table of {skillName, price, npcLevel, playerLevel, canTrain} entries.
        api["getTrainingServices"] = [luaState = context.mLua](std::string_view npcName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
                return result;

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find NPC near player by name
            std::string searchName(npcName);
            std::transform(searchName.begin(), searchName.end(), searchName.begin(), ::tolower);

            MWWorld::Ptr npc;
            std::vector<MWWorld::Ptr> actors;
            MWBase::Environment::get().getMechanicsManager()->getActorsInRange(
                player.getRefData().getPosition().asVec3(), 1000.0f, actors);
            for (const auto& actor : actors)
            {
                if (actor == player)
                    continue;
                std::string actorName(actor.getClass().getName(actor));
                std::string actorNameLower = actorName;
                std::transform(actorNameLower.begin(), actorNameLower.end(), actorNameLower.begin(), ::tolower);
                if (actorNameLower.find(searchName) != std::string::npos)
                {
                    npc = actor;
                    break;
                }
            }

            if (npc.isEmpty())
                return result;

            // Check NPC offers training services
            if (!npc.getClass().isNpc())
                return result;

            int services = npc.getClass().getServices(npc);
            if (!(services & ESM::NPC::Training))
                return result;

            const auto& store = MWBase::Environment::get().getESMStore();
            const MWWorld::Store<ESM::GameSetting>& gmst = store->get<ESM::GameSetting>();
            const MWWorld::Store<ESM::Skill>& skillStore = store->get<ESM::Skill>();

            // Find the NPC's top 3 skills (same logic as TrainingWindow::setPtr)
            constexpr size_t maxSkills = 3;
            std::vector<std::pair<const ESM::Skill*, float>> skills;
            skills.reserve(maxSkills);

            const auto sortByValue
                = [](const std::pair<const ESM::Skill*, float>& lhs,
                      const std::pair<const ESM::Skill*, float>& rhs) { return lhs.second > rhs.second; };

            const MWMechanics::NpcStats& actorStats = npc.getClass().getNpcStats(npc);
            for (const ESM::Skill& skill : skillStore)
            {
                float value;
                if (Settings::game().mTrainersTrainingSkillsBasedOnBaseSkill)
                    value = actorStats.getSkill(skill.mId).getBase();
                else
                    value = actorStats.getSkill(skill.mId).getModified();

                if (skills.size() < maxSkills)
                {
                    skills.emplace_back(&skill, value);
                    std::stable_sort(skills.begin(), skills.end(), sortByValue);
                }
                else
                {
                    auto& lowest = skills[maxSkills - 1];
                    if (lowest.second < value)
                    {
                        lowest.first = &skill;
                        lowest.second = value;
                        std::stable_sort(skills.begin(), skills.end(), sortByValue);
                    }
                }
            }

            MWMechanics::NpcStats& pcStats = player.getClass().getNpcStats(player);
            int playerGold = player.getClass().getContainerStore(player).count(MWWorld::ContainerStore::sGoldId);

            int idx = 1;
            for (const auto& [skill, npcSkillValue] : skills)
            {
                float playerSkillBase = pcStats.getSkill(skill->mId).getBase();
                int price = static_cast<int>(
                    playerSkillBase * gmst.find("iTrainingMod")->mValue.getInteger());
                price = std::max(1, price);
                price = MWBase::Environment::get().getMechanicsManager()->getBarterOffer(npc, price, true);

                // Check if player can train: NPC skill must be > player skill
                bool npcCanTeach = npcSkillValue > playerSkillBase;

                // Check governing attribute limit
                float govAttr = pcStats.getAttribute(
                    ESM::Attribute::indexToRefId(skill->mData.mAttribute)).getModified();
                bool attrOk = playerSkillBase < govAttr;

                bool canTrain = npcCanTeach && attrOk && playerGold >= price;

                sol::table entry(lua2, sol::create);
                entry["skillName"] = skill->mName;
                entry["price"] = price;
                entry["npcLevel"] = static_cast<int>(npcSkillValue);
                entry["playerLevel"] = static_cast<int>(playerSkillBase);
                entry["canTrain"] = canTrain;
                result[idx++] = entry;
            }

            return result;
        };

        // Train a specific skill from a nearby NPC trainer.
        // Returns {success, skillName, newLevel, price, message}.
        api["trainSkill"] = [luaState = context.mLua](std::string_view npcName, std::string_view skillName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find NPC near player by name
            std::string searchName(npcName);
            std::transform(searchName.begin(), searchName.end(), searchName.begin(), ::tolower);

            MWWorld::Ptr npc;
            std::vector<MWWorld::Ptr> actors;
            MWBase::Environment::get().getMechanicsManager()->getActorsInRange(
                player.getRefData().getPosition().asVec3(), 1000.0f, actors);
            for (const auto& actor : actors)
            {
                if (actor == player)
                    continue;
                std::string actorName(actor.getClass().getName(actor));
                std::string actorNameLower = actorName;
                std::transform(actorNameLower.begin(), actorNameLower.end(), actorNameLower.begin(), ::tolower);
                if (actorNameLower.find(searchName) != std::string::npos)
                {
                    npc = actor;
                    break;
                }
            }

            if (npc.isEmpty())
            {
                result["success"] = false;
                result["message"] = "Trainer not found nearby: " + std::string(npcName);
                return result;
            }

            if (!npc.getClass().isNpc())
            {
                result["success"] = false;
                result["message"] = std::string(npc.getClass().getName(npc)) + " is not an NPC";
                return result;
            }

            int services = npc.getClass().getServices(npc);
            if (!(services & ESM::NPC::Training))
            {
                result["success"] = false;
                result["message"] = std::string(npc.getClass().getName(npc)) + " does not offer training services";
                return result;
            }

            // Find the skill by name
            const auto& store = MWBase::Environment::get().getESMStore();
            const MWWorld::Store<ESM::GameSetting>& gmst = store->get<ESM::GameSetting>();
            const MWWorld::Store<ESM::Skill>& skillStore = store->get<ESM::Skill>();

            std::string searchSkill(skillName);
            std::transform(searchSkill.begin(), searchSkill.end(), searchSkill.begin(), ::tolower);

            const ESM::Skill* foundSkill = nullptr;
            for (const ESM::Skill& skill : skillStore)
            {
                std::string sName = skill.mName;
                std::transform(sName.begin(), sName.end(), sName.begin(), ::tolower);
                if (sName.find(searchSkill) != std::string::npos)
                {
                    foundSkill = &skill;
                    break;
                }
            }

            if (!foundSkill)
            {
                result["success"] = false;
                result["message"] = "Unknown skill: " + std::string(skillName);
                return result;
            }

            MWMechanics::NpcStats& pcStats = player.getClass().getNpcStats(player);
            const MWMechanics::NpcStats& actorStats = npc.getClass().getNpcStats(npc);

            float playerSkillBase = pcStats.getSkill(foundSkill->mId).getBase();

            // Get NPC's skill value (respecting the setting)
            float npcSkillValue;
            if (Settings::game().mTrainersTrainingSkillsBasedOnBaseSkill)
                npcSkillValue = actorStats.getSkill(foundSkill->mId).getBase();
            else
                npcSkillValue = actorStats.getSkill(foundSkill->mId).getModified();

            // Validate: NPC skill > player skill
            if (npcSkillValue <= playerSkillBase)
            {
                result["success"] = false;
                result["message"] = "Your " + foundSkill->mName + " skill (" + std::to_string(static_cast<int>(playerSkillBase))
                    + ") is already at or above the trainer's level (" + std::to_string(static_cast<int>(npcSkillValue)) + ")";
                return result;
            }

            // Validate: player skill < governing attribute
            float govAttr = pcStats.getAttribute(
                ESM::Attribute::indexToRefId(foundSkill->mData.mAttribute)).getModified();
            if (playerSkillBase >= govAttr)
            {
                result["success"] = false;
                result["message"] = "Cannot train " + foundSkill->mName + " above governing attribute ("
                    + std::to_string(static_cast<int>(govAttr)) + ")";
                return result;
            }

            // Calculate price
            int price = static_cast<int>(
                playerSkillBase * gmst.find("iTrainingMod")->mValue.getInteger());
            price = std::max(1, price);
            price = MWBase::Environment::get().getMechanicsManager()->getBarterOffer(npc, price, true);

            // Check gold
            int playerGold = player.getClass().getContainerStore(player).count(MWWorld::ContainerStore::sGoldId);
            if (playerGold < price)
            {
                result["success"] = false;
                result["message"] = "Not enough gold. Need " + std::to_string(price) + ", have "
                    + std::to_string(playerGold);
                return result;
            }

            // Increase skill
            MWBase::Environment::get().getLuaManager()->skillLevelUp(player, foundSkill->mId, "trainer");

            // Remove gold from player
            player.getClass().getContainerStore(player).remove(MWWorld::ContainerStore::sGoldId, price);

            // Add gold to NPC trading gold pool
            MWMechanics::NpcStats& npcStats = npc.getClass().getNpcStats(npc);
            npcStats.setGoldPool(npcStats.getGoldPool() + price);

            // Advance time by 2 hours (same as training window)
            MWBase::Environment::get().getMechanicsManager()->rest(2, false);
            world->advanceTime(2);

            float newLevel = pcStats.getSkill(foundSkill->mId).getBase();

            result["success"] = true;
            result["skillName"] = foundSkill->mName;
            result["newLevel"] = static_cast<int>(newLevel);
            result["price"] = price;
            result["message"] = "Trained " + foundSkill->mName + " to " + std::to_string(static_cast<int>(newLevel))
                + " (cost: " + std::to_string(price) + " gold)";

            Log(Debug::Info) << "Bridge: Trained " << foundSkill->mName << " to " << static_cast<int>(newLevel)
                << " (cost: " << price << " gold)";

            return result;
        };

        // --- Alchemy bindings ---

        // Preview what effects a combination of ingredients would produce without consuming them.
        // ingredientIds: table of 2-4 ingredient record ID strings.
        // Returns {success, effects=[{name, magnitude, duration}...]} or {success=false, message=...}
        api["previewPotion"] = [luaState = context.mLua](sol::table ingredientIds) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            MWMechanics::Alchemy alchemy;
            alchemy.setAlchemist(player);

            // Check for mortar & pestle
            bool hasMortar = false;
            for (auto it = alchemy.beginTools(); it != alchemy.endTools(); ++it)
            {
                if (!it->isEmpty() && it->get<ESM::Apparatus>()->mBase->mData.mType == ESM::Apparatus::MortarPestle)
                {
                    hasMortar = true;
                    break;
                }
            }
            if (!hasMortar)
            {
                result["success"] = false;
                result["message"] = "No Mortar & Pestle in inventory";
                return result;
            }

            // Add ingredients
            MWWorld::ContainerStore& store = player.getClass().getContainerStore(player);
            int addedCount = 0;
            for (auto& kv : ingredientIds)
            {
                std::string ingId = kv.second.as<std::string>();
                bool found = false;
                for (auto it = store.begin(MWWorld::ContainerStore::Type_Ingredient); it != store.end(); ++it)
                {
                    if (it->getCellRef().getRefId() == ESM::RefId::stringRefId(ingId))
                    {
                        int slot = alchemy.addIngredient(*it);
                        if (slot >= 0)
                        {
                            addedCount++;
                            found = true;
                        }
                        break;
                    }
                }
                if (!found)
                {
                    result["success"] = false;
                    result["message"] = "Ingredient not found in inventory: " + ingId;
                    return result;
                }
            }

            if (addedCount < 2)
            {
                result["success"] = false;
                result["message"] = "Need at least 2 different ingredients";
                return result;
            }

            // List effects
            std::vector<MWMechanics::EffectKey> effects = alchemy.listEffects();
            if (effects.empty())
            {
                result["success"] = true;
                result["message"] = "No shared effects between these ingredients";
                sol::table effectsTable(lua2, sol::create);
                result["effects"] = effectsTable;
                return result;
            }

            sol::table effectsTable(lua2, sol::create);
            int idx = 1;
            for (const auto& effectKey : effects)
            {
                sol::table entry(lua2, sol::create);
                entry["name"] = effectKey.toString();
                effectsTable[idx++] = entry;
            }

            result["success"] = true;
            result["effects"] = effectsTable;
            result["message"] = std::to_string(effects.size()) + " effect(s) found";
            return result;
        };

        // Brew a potion from ingredients.
        // potionName: string name for the potion
        // ingredientIds: table of 2-4 ingredient record ID strings
        // Returns {success, message, result} where result is "Success", "RandomFailure", etc.
        api["brewPotion"] = [luaState = context.mLua](
                                 std::string_view potionName, sol::table ingredientIds) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            if (potionName.empty())
            {
                result["success"] = false;
                result["message"] = "Potion name cannot be empty";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            MWMechanics::Alchemy alchemy;
            alchemy.setAlchemist(player);

            // Check for mortar & pestle
            bool hasMortar = false;
            for (auto it = alchemy.beginTools(); it != alchemy.endTools(); ++it)
            {
                if (!it->isEmpty() && it->get<ESM::Apparatus>()->mBase->mData.mType == ESM::Apparatus::MortarPestle)
                {
                    hasMortar = true;
                    break;
                }
            }
            if (!hasMortar)
            {
                result["success"] = false;
                result["message"] = "No Mortar & Pestle in inventory";
                return result;
            }

            // Add ingredients
            MWWorld::ContainerStore& store = player.getClass().getContainerStore(player);
            int addedCount = 0;
            for (auto& kv : ingredientIds)
            {
                std::string ingId = kv.second.as<std::string>();
                bool found = false;
                for (auto it = store.begin(MWWorld::ContainerStore::Type_Ingredient); it != store.end(); ++it)
                {
                    if (it->getCellRef().getRefId() == ESM::RefId::stringRefId(ingId))
                    {
                        int slot = alchemy.addIngredient(*it);
                        if (slot >= 0)
                        {
                            addedCount++;
                            found = true;
                        }
                        break;
                    }
                }
                if (!found)
                {
                    result["success"] = false;
                    result["message"] = "Ingredient not found in inventory: " + ingId;
                    return result;
                }
            }

            if (addedCount < 2)
            {
                result["success"] = false;
                result["message"] = "Need at least 2 different ingredients";
                return result;
            }

            // Create the potion
            int count = 1;
            MWMechanics::Alchemy::Result alchemyResult = alchemy.create(std::string(potionName), count);

            std::string resultName;
            switch (alchemyResult)
            {
                case MWMechanics::Alchemy::Result_Success:
                    resultName = "Success";
                    break;
                case MWMechanics::Alchemy::Result_NoMortarAndPestle:
                    resultName = "NoMortarAndPestle";
                    break;
                case MWMechanics::Alchemy::Result_LessThanTwoIngredients:
                    resultName = "LessThanTwoIngredients";
                    break;
                case MWMechanics::Alchemy::Result_NoName:
                    resultName = "NoName";
                    break;
                case MWMechanics::Alchemy::Result_NoEffects:
                    resultName = "NoEffects";
                    break;
                case MWMechanics::Alchemy::Result_RandomFailure:
                    resultName = "RandomFailure";
                    break;
            }

            result["success"] = (alchemyResult == MWMechanics::Alchemy::Result_Success);
            result["result"] = resultName;

            if (alchemyResult == MWMechanics::Alchemy::Result_Success)
            {
                result["message"] = "Successfully brewed: " + std::string(potionName);
                Log(Debug::Info) << "Bridge: Brewed potion: " << potionName;
            }
            else if (alchemyResult == MWMechanics::Alchemy::Result_RandomFailure)
            {
                result["message"]
                    = "Failed to brew potion (skill check failed). Ingredients consumed.";
                Log(Debug::Info) << "Bridge: Potion brewing failed (random failure)";
            }
            else if (alchemyResult == MWMechanics::Alchemy::Result_NoEffects)
            {
                result["message"]
                    = "No shared effects between ingredients. Ingredients consumed.";
            }
            else
            {
                result["message"] = "Brewing failed: " + resultName;
            }

            return result;
        };

        // --- Security bindings (lockpicking and trap disarming) ---

        // Helper lambda to find a container or door near the player by name
        auto findLockableNearPlayer = [](std::string_view targetName, float maxRange) -> MWWorld::Ptr {
            auto world = MWBase::Environment::get().getWorld();
            MWWorld::Ptr player = world->getPlayerPtr();
            osg::Vec3f playerPos = player.getRefData().getPosition().asVec3();

            std::string searchName(targetName);
            std::transform(searchName.begin(), searchName.end(), searchName.begin(), ::tolower);

            MWWorld::Ptr bestMatch;
            float bestDist = maxRange;

            MWWorld::CellStore* cell = player.getCell();
            if (!cell)
                return bestMatch;

            // Search containers
            cell->forEachType<ESM::Container>([&](MWWorld::Ptr ptr) {
                std::string name(ptr.getClass().getName(ptr));
                std::string nameLower = name;
                std::transform(nameLower.begin(), nameLower.end(), nameLower.begin(), ::tolower);
                if (nameLower.find(searchName) != std::string::npos)
                {
                    float dist = (ptr.getRefData().getPosition().asVec3() - playerPos).length();
                    if (dist < bestDist)
                    {
                        bestDist = dist;
                        bestMatch = ptr;
                    }
                }
                return true;
            });

            // Search doors
            cell->forEachType<ESM::Door>([&](MWWorld::Ptr ptr) {
                std::string name(ptr.getClass().getName(ptr));
                std::string nameLower = name;
                std::transform(nameLower.begin(), nameLower.end(), nameLower.begin(), ::tolower);
                if (nameLower.find(searchName) != std::string::npos)
                {
                    float dist = (ptr.getRefData().getPosition().asVec3() - playerPos).length();
                    if (dist < bestDist)
                    {
                        bestDist = dist;
                        bestMatch = ptr;
                    }
                }
                return true;
            });

            return bestMatch;
        };

        // Pick a lock on a nearby container or door.
        // Uses the player's best lockpick and Security skill.
        // Returns {success, message, lockLevel, usesLeft}
        api["pickLock"] = [luaState = context.mLua, findLockableNearPlayer](std::string_view targetName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find target
            MWWorld::Ptr target = findLockableNearPlayer(targetName, 500.0f);
            if (target.isEmpty())
            {
                result["success"] = false;
                result["message"] = "Could not find lockable object nearby: " + std::string(targetName);
                return result;
            }

            // Check if locked
            int lockLevel = target.getCellRef().getLockLevel();
            if (lockLevel <= 0 || !target.getCellRef().isLocked())
            {
                result["success"] = false;
                result["message"] = std::string(target.getClass().getName(target)) + " is not locked";
                return result;
            }

            result["lockLevel"] = lockLevel;

            // Find best lockpick in player inventory
            MWWorld::Ptr bestLockpick;
            float bestQuality = -1;
            auto& store = player.getClass().getContainerStore(player);
            for (MWWorld::ContainerStoreIterator it = store.begin(MWWorld::ContainerStore::Type_Lockpick);
                 it != store.end(); ++it)
            {
                float quality = it->get<ESM::Lockpick>()->mBase->mData.mQuality;
                if (quality > bestQuality)
                {
                    bestQuality = quality;
                    bestLockpick = *it;
                }
            }

            if (bestLockpick.isEmpty())
            {
                result["success"] = false;
                result["message"] = "No lockpick in inventory";
                return result;
            }

            std::string lockpickName(bestLockpick.getClass().getName(bestLockpick));

            // Attempt to pick
            MWMechanics::Security security(player);
            std::string_view resultMessage;
            std::string_view resultSound;
            security.pickLock(target, bestLockpick, resultMessage, resultSound);

            // Play sound
            MWBase::Environment::get().getWindowManager()->playSound(ESM::RefId::stringRefId(resultSound));

            // Check result
            bool unlocked = (target.getCellRef().getLockLevel() <= 0 || !target.getCellRef().isLocked());
            result["success"] = unlocked;

            // Get remaining uses (lockpick may have been destroyed if uses hit 0)
            int usesLeft = 0;
            try
            {
                if (!bestLockpick.isEmpty() && bestLockpick.getCellRef().getCount() > 0)
                    usesLeft = bestLockpick.getClass().getItemHealth(bestLockpick);
            }
            catch (...)
            {
                usesLeft = 0;
            }
            result["usesLeft"] = usesLeft;

            std::string targetObjName(target.getClass().getName(target));
            if (unlocked)
            {
                result["message"] = "Successfully picked lock on " + targetObjName
                    + " (lock level " + std::to_string(lockLevel) + ") using " + lockpickName;
            }
            else
            {
                result["message"] = "Failed to pick lock on " + targetObjName
                    + " (lock level " + std::to_string(lockLevel) + ") using " + lockpickName
                    + (usesLeft > 0
                        ? " (" + std::to_string(usesLeft) + " uses remaining)"
                        : " (lockpick broke!)");
            }

            Log(Debug::Info) << "Bridge: pickLock — " << result.get<std::string>("message");

            return result;
        };

        // Disarm a trap on a nearby container or door.
        // Uses the player's best probe and Security skill.
        // Returns {success, message, usesLeft}
        api["disarmTrap"] = [luaState = context.mLua, findLockableNearPlayer](std::string_view targetName) -> sol::table {
            sol::state_view lua2 = luaState->unsafeState();
            sol::table result(lua2, sol::create);

            auto world = MWBase::Environment::get().getWorld();
            if (!world)
            {
                result["success"] = false;
                result["message"] = "World not available";
                return result;
            }

            MWWorld::Ptr player = world->getPlayerPtr();

            // Find target
            MWWorld::Ptr target = findLockableNearPlayer(targetName, 500.0f);
            if (target.isEmpty())
            {
                result["success"] = false;
                result["message"] = "Could not find object nearby: " + std::string(targetName);
                return result;
            }

            // Check if trapped
            if (target.getCellRef().getTrap().empty())
            {
                result["success"] = false;
                result["message"] = std::string(target.getClass().getName(target)) + " is not trapped";
                return result;
            }

            // Find best probe in player inventory
            MWWorld::Ptr bestProbe;
            float bestQuality = -1;
            auto& store = player.getClass().getContainerStore(player);
            for (MWWorld::ContainerStoreIterator it = store.begin(MWWorld::ContainerStore::Type_Probe);
                 it != store.end(); ++it)
            {
                float quality = it->get<ESM::Probe>()->mBase->mData.mQuality;
                if (quality > bestQuality)
                {
                    bestQuality = quality;
                    bestProbe = *it;
                }
            }

            if (bestProbe.isEmpty())
            {
                result["success"] = false;
                result["message"] = "No probe in inventory";
                return result;
            }

            std::string probeName(bestProbe.getClass().getName(bestProbe));

            // Attempt to disarm
            MWMechanics::Security security(player);
            std::string_view resultMessage;
            std::string_view resultSound;
            security.probeTrap(target, bestProbe, resultMessage, resultSound);

            // Play sound
            MWBase::Environment::get().getWindowManager()->playSound(ESM::RefId::stringRefId(resultSound));

            // Check result
            bool disarmed = target.getCellRef().getTrap().empty();
            result["success"] = disarmed;

            // Get remaining uses (probe may have been destroyed if uses hit 0)
            int usesLeft = 0;
            try
            {
                if (!bestProbe.isEmpty() && bestProbe.getCellRef().getCount() > 0)
                    usesLeft = bestProbe.getClass().getItemHealth(bestProbe);
            }
            catch (...)
            {
                usesLeft = 0;
            }
            result["usesLeft"] = usesLeft;

            std::string targetObjName(target.getClass().getName(target));
            if (disarmed)
            {
                result["message"] = "Successfully disarmed trap on " + targetObjName + " using " + probeName;
            }
            else
            {
                result["message"] = "Failed to disarm trap on " + targetObjName + " using " + probeName
                    + (usesLeft > 0
                        ? " (" + std::to_string(usesLeft) + " uses remaining)"
                        : " (probe broke!)");
            }

            Log(Debug::Info) << "Bridge: disarmTrap — " << result.get<std::string>("message");

            return result;
        };

        return LuaUtil::makeReadOnly(api);
    }
}
