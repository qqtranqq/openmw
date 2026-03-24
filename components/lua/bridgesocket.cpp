#include "bridgesocket.hpp"

#include <components/debug/debuglog.hpp>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#endif

#include <algorithm>
#include <cstring>

namespace
{
#ifdef _WIN32
    using SocketType = SOCKET;
    constexpr SocketType InvalidSocket = INVALID_SOCKET;
#else
    using SocketType = int;
    constexpr SocketType InvalidSocket = -1;
#endif

    void setNonBlocking(SocketType sock)
    {
#ifdef _WIN32
        u_long mode = 1;
        ioctlsocket(sock, FIONBIO, &mode);
#else
        int flags = fcntl(sock, F_GETFL, 0);
        if (flags != -1)
        {
            fcntl(sock, F_SETFL, flags | O_NONBLOCK);
        }
#endif
    }

    bool isWouldBlock()
    {
#ifdef _WIN32
        return WSAGetLastError() == WSAEWOULDBLOCK;
#else
        return errno == EAGAIN || errno == EWOULDBLOCK;
#endif
    }

    void closeSocket(SocketType sock)
    {
#ifdef _WIN32
        closesocket(sock);
#else
        close(sock);
#endif
    }
}

namespace LuaUtil
{
    BridgeSocket::BridgeSocket() = default;

    BridgeSocket::~BridgeSocket()
    {
        stop();
    }

    void BridgeSocket::start(uint16_t port)
    {
        if (mRunning)
        {
            return;
        }

#ifdef _WIN32
        static bool wsaInitialized = false;
        if (!wsaInitialized)
        {
            WSADATA wsaData;
            if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
            {
                Log(Debug::Error) << "Bridge: WSAStartup failed";
                return;
            }
            wsaInitialized = true;
        }
#endif

        SocketType listenSock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listenSock == InvalidSocket)
        {
            Log(Debug::Error) << "Bridge: Failed to create listen socket";
            return;
        }

        int opt = 1;
        setsockopt(listenSock, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&opt), sizeof(opt));

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

        if (bind(listenSock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0)
        {
            Log(Debug::Error) << "Bridge: Failed to bind to port " << port;
            closeSocket(listenSock);
            return;
        }

        if (listen(listenSock, 1) != 0)
        {
            Log(Debug::Error) << "Bridge: Failed to listen on port " << port;
            closeSocket(listenSock);
            return;
        }

        setNonBlocking(listenSock);

        mListenSocket = static_cast<intptr_t>(listenSock);
        mPort = port;
        mRunning = true;

        Log(Debug::Info) << "Bridge: Listening on 127.0.0.1:" << port;
    }

    void BridgeSocket::stop()
    {
        if (!mRunning)
        {
            return;
        }

        closeClient();

        if (mListenSocket != static_cast<intptr_t>(InvalidSocket))
        {
            closeSocket(static_cast<SocketType>(mListenSocket));
            mListenSocket = static_cast<intptr_t>(InvalidSocket);
        }

        mRunning = false;
        mRecvBuffer.clear();
        mSendBuffer.clear();

#ifdef _WIN32
        WSACleanup();
#endif

        Log(Debug::Info) << "Bridge: Stopped";
    }

    void BridgeSocket::update()
    {
        if (!mRunning)
        {
            return;
        }

        acceptConnection();
        readData();
        writeData();
    }

    void BridgeSocket::acceptConnection()
    {
        if (mClientSocket != static_cast<intptr_t>(InvalidSocket))
        {
            return;
        }

        SocketType listenSock = static_cast<SocketType>(mListenSocket);
        sockaddr_in clientAddr{};
        socklen_t addrLen = sizeof(clientAddr);

        SocketType clientSock = accept(listenSock, reinterpret_cast<sockaddr*>(&clientAddr), &addrLen);
        if (clientSock == InvalidSocket)
        {
            return;
        }

        setNonBlocking(clientSock);
        mClientSocket = static_cast<intptr_t>(clientSock);

        Log(Debug::Info) << "Bridge: Client connected";
    }

    void BridgeSocket::readData()
    {
        if (mClientSocket == static_cast<intptr_t>(InvalidSocket))
        {
            return;
        }

        SocketType clientSock = static_cast<SocketType>(mClientSocket);

        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(clientSock, &readSet);

        timeval timeout{};
        timeout.tv_sec = 0;
        timeout.tv_usec = 0;

        int ready = select(static_cast<int>(clientSock) + 1, &readSet, nullptr, nullptr, &timeout);
        if (ready <= 0)
        {
            return;
        }

        char buf[4096];
        int received = recv(clientSock, buf, sizeof(buf), 0);

        if (received > 0)
        {
            mRecvBuffer.append(buf, static_cast<std::size_t>(received));
        }
        else if (received == 0)
        {
            closeClient();
        }
        else
        {
            if (!isWouldBlock())
            {
                closeClient();
            }
        }
    }

    std::vector<std::string> BridgeSocket::poll()
    {
        std::vector<std::string> messages;

        std::size_t pos = 0;
        std::size_t found;
        while ((found = mRecvBuffer.find('\n', pos)) != std::string::npos)
        {
            messages.emplace_back(mRecvBuffer.substr(pos, found - pos));
            pos = found + 1;
        }

        if (pos > 0)
        {
            mRecvBuffer.erase(0, pos);
        }

        return messages;
    }

    void BridgeSocket::send(std::string_view msg)
    {
        mSendBuffer.append(msg);
        mSendBuffer.push_back('\n');
    }

    void BridgeSocket::writeData()
    {
        if (mClientSocket == static_cast<intptr_t>(InvalidSocket) || mSendBuffer.empty())
        {
            return;
        }

        SocketType clientSock = static_cast<SocketType>(mClientSocket);

        fd_set writeSet;
        FD_ZERO(&writeSet);
        FD_SET(clientSock, &writeSet);

        timeval timeout{};
        timeout.tv_sec = 0;
        timeout.tv_usec = 0;

        int ready = select(static_cast<int>(clientSock) + 1, nullptr, &writeSet, nullptr, &timeout);
        if (ready <= 0)
        {
            return;
        }

        int sent = ::send(clientSock, mSendBuffer.data(), static_cast<int>(mSendBuffer.size()), 0);

        if (sent > 0)
        {
            mSendBuffer.erase(0, static_cast<std::size_t>(sent));
        }
        else if (sent < 0)
        {
            if (!isWouldBlock())
            {
                closeClient();
            }
        }
    }

    void BridgeSocket::closeClient()
    {
        if (mClientSocket == static_cast<intptr_t>(InvalidSocket))
        {
            return;
        }

        closeSocket(static_cast<SocketType>(mClientSocket));
        mClientSocket = static_cast<intptr_t>(InvalidSocket);

        Log(Debug::Info) << "Bridge: Client disconnected";
    }

    bool BridgeSocket::isConnected() const
    {
        return mClientSocket != static_cast<intptr_t>(InvalidSocket);
    }

    uint16_t BridgeSocket::getPort() const
    {
        return mPort;
    }
}
