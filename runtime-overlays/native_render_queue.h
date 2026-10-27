#pragma once
// NATIVE_RENDER_QUEUE_V1: bounded, in-order hand-off from one producer thread (the main thread: drain servicing and frame
// submission) to one executor thread (the GL thread, which owns the context). See tools/fix_render_queue.py.
//
// Item k lives in slot k % capacity. The producer may write slot k only after item k - capacity has completed, so an
// executing item and a slot being filled never share storage. Publishing and completing both happen under the mutex, so
// everything the producer wrote before Publish() is visible to the executor, and everything the executor wrote before
// it completed is visible to the producer's next Acquire().
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <mutex>
#include <vector>

namespace nwii::runtime::native_render_queue {

template <class Item>
class OrderedQueue {
public:
    explicit OrderedQueue(size_t capacity) : slots_(capacity ? capacity : 1) {}

    // Producer: waits until a slot is free and returns it. Only the producer touches it until Publish().
    Item& Acquire() {
        std::unique_lock<std::mutex> lock(mutex_);
        space_.wait(lock, [&] { return published_ - done_ < slots_.size(); });
        return slots_[published_ % slots_.size()];
    }

    // Producer: hands the slot returned by the last Acquire() to the executor.
    void Publish() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            ++published_;
        }
        work_.notify_one();
    }

    // Producer: waits until every published item has completed. The executor is then idle until the next Publish().
    void Flush() {
        std::unique_lock<std::mutex> lock(mutex_);
        idle_.wait(lock, [&] { return done_ == published_; });
    }

    // Executor thread: runs items in order. Returns after Stop() once nothing is left to run.
    void Run(const std::function<void(Item&)>& execute) {
        for (;;) {
            size_t k = 0;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                work_.wait(lock, [&] { return stop_ || done_ < published_; });
                if (done_ == published_) return;
                k = done_;
            }
            execute(slots_[k % slots_.size()]); // unlocked: the producer does not reuse this slot until done_ moves past it
            {
                std::lock_guard<std::mutex> lock(mutex_);
                ++done_;
            }
            space_.notify_one();
            idle_.notify_all();
        }
    }

    // Any thread: ask Run() to return once the queue is empty.
    void Stop() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = true;
        }
        work_.notify_all();
    }

    size_t Capacity() const { return slots_.size(); }

    // Items published and not yet completed. Only meaningful as a snapshot.
    size_t InFlight() {
        std::lock_guard<std::mutex> lock(mutex_);
        return published_ - done_;
    }

private:
    std::vector<Item> slots_;
    std::mutex mutex_;
    std::condition_variable work_, space_, idle_;
    size_t published_ = 0, done_ = 0;
    bool stop_ = false;
};

} // namespace nwii::runtime::native_render_queue
