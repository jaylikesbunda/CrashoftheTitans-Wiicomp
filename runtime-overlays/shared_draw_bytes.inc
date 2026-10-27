// NATIVE_SHARED_DRAW_BYTES_V1: matrix snapshots share immutable encoded bytes.
// Mutable access detaches; no cached draw can mutate another snapshot's bytes.
class NativeDrawBytes {
    std::shared_ptr<std::vector<uint8_t>> storage;
    std::vector<uint8_t> small;
    void promote(size_t bytes){if(bytes>=4096 && !storage)storage=std::make_shared<std::vector<uint8_t>>(std::move(small));}
    const std::vector<uint8_t>& view() const {
        return storage?*storage:small;
    }
    std::vector<uint8_t>& edit() {
        if(storage && !storage.unique())storage=std::make_shared<std::vector<uint8_t>>(*storage);
        return storage?*storage:small;
    }
public:
    using iterator=std::vector<uint8_t>::iterator;
    using const_iterator=std::vector<uint8_t>::const_iterator;
    size_t size()const{return view().size();}
    bool empty()const{return view().empty();}
    const uint8_t* data()const{return view().data();}
    uint8_t* data(){return edit().data();}
    const_iterator begin()const{return view().begin();}
    const_iterator end()const{return view().end();}
    iterator begin(){return edit().begin();}
    iterator end(){return edit().end();}
    const uint8_t& operator[](size_t i)const{return view()[i];}
    uint8_t& operator[](size_t i){return edit()[i];}
    void clear(){if(storage && storage.unique())storage->clear();else storage.reset();small.clear();}
    size_t allocated_bytes()const{return small.capacity()+(storage?storage->capacity():0);}
    void swap(NativeDrawBytes& other) noexcept {storage.swap(other.storage);small.swap(other.small);}
    void reserve(size_t n){promote(n);edit().reserve(n);}
    void resize(size_t n,uint8_t value=0){promote(n);edit().resize(n,value);}
    void push_back(uint8_t value){promote(size()+1);edit().push_back(value);}
    void pop_back(){edit().pop_back();}
    template<class Iterator>void assign(Iterator first,Iterator last){promote(size_t(std::distance(first,last)));edit().assign(first,last);}
    iterator insert(const_iterator pos,std::initializer_list<uint8_t> values){
        const size_t offset=size_t(pos-view().begin());auto& bytes=edit();
        return bytes.insert(bytes.begin()+offset,values);
    }
    operator const std::vector<uint8_t>&()const{return view();}
    friend bool operator==(const NativeDrawBytes& a,const NativeDrawBytes& b){return a.view()==b.view();}
};
