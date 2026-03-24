#ifndef COMPONENTS_LUA_BRIDGESOCKET_H
#define COMPONENTS_LUA_BRIDGESOCKET_H

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace LuaUtil
{
    class BridgeSocket
    {
    public:
        BridgeSocket();
        ~BridgeSocket();

        BridgeSocket(const BridgeSocket&) = delete;
        BridgeSocket& operator=(const BridgeSocket&) = delete;

        void start(uint16_t port);
        void stop();
        void update(); // call once per frame: accept connections, read/write buffered data
        std::vector<std::string> poll(); // returns complete newline-delimited messages
        void send(std::string_view msg); // queues message (appends newline)
        bool isConnected() const;
        uint16_t getPort() const;

    private:
        void acceptConnection();
        void readData();
        void writeData();
        void closeClient();

        uint16_t mPort = 0;
        bool mRunning = false;

        // Platform socket handles stored as intptr_t to avoid platform headers in the header
        intptr_t mListenSocket = -1;
        intptr_t mClientSocket = -1;

        std::string mRecvBuffer;
        std::string mSendBuffer;
    };
}

#endif // COMPONENTS_LUA_BRIDGESOCKET_H
