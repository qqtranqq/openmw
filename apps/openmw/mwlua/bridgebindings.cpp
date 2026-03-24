#include "bridgebindings.hpp"

#include <components/lua/bridgesocket.hpp>

#include "context.hpp"

namespace MWLua
{
    // Single shared bridge socket instance
    static std::unique_ptr<LuaUtil::BridgeSocket> sBridgeSocket;

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
            sol::state_view lua = luaState->unsafeState();
            sol::table result(lua, sol::create);
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

        return api;
    }
}
